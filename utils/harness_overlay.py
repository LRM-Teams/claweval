"""Per-task harness overlay: validation and container injection.

An overlay is a mountable directory produced by the harness-evolution loop
(see docs/pi-task-harness-evolution-spec.md §3). Allowed contents:

    SYSTEM_APPENDIX.md   short appendix appended to the Pi message
    settings.json        allowlisted Pi settings fragment (shallow merge)
    skills/<name>/...    extra or overriding Pi skill bundles

Anything else is rejected by ``validate_overlay``.
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_APPENDIX_BYTES = 4096
MAX_SKILL_FILES = 32
SETTINGS_ALLOWLIST = ("thinkingDefault",)
ALLOWED_TOP_LEVEL = ("SYSTEM_APPENDIX.md", "settings.json", "skills")
# Overlay skills must never smuggle grading assets into the container.
FORBIDDEN_PATH_PARTS = ("gt",)
FORBIDDEN_NAME_SUBSTRINGS = ("grade",)

_CONTAINER_SETTINGS_TMP = "/tmp/_overlay_settings.json"


class OverlayValidationError(ValueError):
    pass


def resolve_overlay_dir(path: str | Path) -> Path:
    """Accept either an overlay dir itself or a candidate dir containing ``overlay/``.

    Symlinks (e.g. ``evolved/<task>/champion``) are resolved.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        raise OverlayValidationError(f"overlay path is not a directory: {path}")
    nested = p / "overlay"
    if nested.is_dir():
        return nested
    return p


def validate_overlay(path: str | Path) -> Path:
    """Validate overlay structure and limits; return the resolved overlay dir."""
    overlay = resolve_overlay_dir(path)

    for entry in overlay.iterdir():
        if entry.name not in ALLOWED_TOP_LEVEL:
            raise OverlayValidationError(
                f"unexpected overlay entry {entry.name!r} "
                f"(allowed: {', '.join(ALLOWED_TOP_LEVEL)})"
            )

    appendix = overlay / "SYSTEM_APPENDIX.md"
    if appendix.exists():
        if not appendix.is_file():
            raise OverlayValidationError("SYSTEM_APPENDIX.md must be a regular file")
        size = appendix.stat().st_size
        if size > MAX_APPENDIX_BYTES:
            raise OverlayValidationError(
                f"SYSTEM_APPENDIX.md is {size} bytes (limit {MAX_APPENDIX_BYTES})"
            )

    settings = overlay / "settings.json"
    if settings.exists():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OverlayValidationError(f"settings.json is not valid JSON: {exc}")
        if not isinstance(data, dict):
            raise OverlayValidationError("settings.json must be a JSON object")
        rejected = sorted(key for key in data if key not in SETTINGS_ALLOWLIST)
        if rejected:
            raise OverlayValidationError(
                f"settings.json keys not in allowlist: {', '.join(rejected)} "
                f"(allowed: {', '.join(SETTINGS_ALLOWLIST)})"
            )

    skills = overlay / "skills"
    if skills.exists():
        if not skills.is_dir():
            raise OverlayValidationError("skills must be a directory")
        files = [f for f in skills.rglob("*") if f.is_file()]
        if len(files) > MAX_SKILL_FILES:
            raise OverlayValidationError(
                f"skills contains {len(files)} files (limit {MAX_SKILL_FILES})"
            )
        for f in files:
            relative = f.relative_to(skills)
            parts_lower = tuple(part.lower() for part in relative.parts)
            if any(part in FORBIDDEN_PATH_PARTS for part in parts_lower):
                raise OverlayValidationError(
                    f"skill file path contains forbidden component: {relative}"
                )
            if any(sub in relative.name.lower() for sub in FORBIDDEN_NAME_SUBSTRINGS):
                raise OverlayValidationError(
                    f"skill file name is forbidden: {relative}"
                )

    return overlay


def load_system_appendix(overlay_dir: str | Path) -> str | None:
    appendix = Path(overlay_dir) / "SYSTEM_APPENDIX.md"
    if not appendix.is_file():
        return None
    text = appendix.read_text(encoding="utf-8").strip()
    return text or None


def overlay_summary(overlay_dir: str | Path) -> dict:
    """Audit record of what an overlay contains (written next to run artifacts)."""
    overlay = Path(overlay_dir)
    files = sorted(
        str(f.relative_to(overlay)) for f in overlay.rglob("*") if f.is_file()
    )
    return {"overlay_dir": str(overlay), "files": files}


def apply_harness_overlay(
    task_id: str,
    overlay_dir: str | Path,
    pi_root: str | None = None,
) -> dict:
    """Inject an already-validated overlay into a running Pi container.

    Must run after ``prepare_pi_run``/``copy_pi_skill`` so overlay skills
    overwrite same-named task skills. Returns a summary of what was applied.
    The SYSTEM_APPENDIX is not handled here; it is injected into the Pi
    message by the caller (see run_batch).
    """
    from .docker_utils import PI_ROOT

    pi_root = pi_root or PI_ROOT
    overlay = validate_overlay(overlay_dir)
    applied = {"skills": [], "settings_keys": [], "appendix": False}

    skills_dir = overlay / "skills"
    if skills_dir.is_dir():
        for skill in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            result = subprocess.run(
                ["docker", "cp", str(skill), f"{task_id}:{pi_root}/agent/skills"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"overlay skill copy failed ({skill.name}):\n{result.stderr}"
                )
            applied["skills"].append(skill.name)

    settings_file = overlay / "settings.json"
    if settings_file.is_file():
        fragment = json.loads(settings_file.read_text(encoding="utf-8"))
        if fragment:
            _merge_container_settings(task_id, fragment, pi_root)
            applied["settings_keys"] = sorted(fragment)

    applied["appendix"] = (overlay / "SYSTEM_APPENDIX.md").is_file()
    logger.info(
        "[%s] Harness overlay applied: skills=%s settings=%s appendix=%s",
        task_id,
        applied["skills"],
        applied["settings_keys"],
        applied["appendix"],
    )
    return applied


def _merge_container_settings(task_id: str, fragment: dict, pi_root: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".json", delete=False
    ) as handle:
        json.dump(fragment, handle, indent=2)
        temporary = handle.name
    try:
        result = subprocess.run(
            ["docker", "cp", temporary, f"{task_id}:{_CONTAINER_SETTINGS_TMP}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"overlay settings copy failed:\n{result.stderr}")
    finally:
        Path(temporary).unlink(missing_ok=True)

    script = (
        "import json; from pathlib import Path; "
        f"p = Path('{pi_root}/agent/settings.json'); "
        "d = json.loads(p.read_text()) if p.exists() else {}; "
        f"f = json.loads(Path('{_CONTAINER_SETTINGS_TMP}').read_text()); "
        "d.update(f); "
        "p.write_text(json.dumps(d, indent=2) + '\\n')"
    )
    result = subprocess.run(
        ["docker", "exec", task_id, "python3", "-c", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"overlay settings merge failed:\n{result.stderr}")
