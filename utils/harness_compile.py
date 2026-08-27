"""Compile a ``harness.spec.json`` into a mountable overlay directory.

    spec + template library  ->  overlay/{SYSTEM_APPENDIX.md, settings.json, skills/**}

Two properties the rest of the system relies on:

* **Determinism.** The same spec compiles to a byte-identical overlay. The bank
  therefore stores specs only (docs/jit-harness-spec.md §5/§7) and rebuilds
  overlays on demand.
* **Fail loud.** Exceeding the appendix budget is an error, never a truncation,
  so a spec that would silently lose its last module cannot reach a container.

Fragment order is fixed to ``preamble -> M -> P -> F -> A``, matching the
paper's runtime dependency order ``M -> P -> F -> A``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from .harness_overlay import MAX_APPENDIX_BYTES, MAX_SKILL_FILES
from .harness_spec import NULL_VALUES, SPEC_FILENAME, dump_spec, validate_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_ROOT = REPO_ROOT / "harness" / "templates"
SEEDS_FILE = REPO_ROOT / "harness" / "seeds.json"
SKILLS_ROOT = REPO_ROOT / "skills"

FRAGMENT_SEPARATOR = "\n\n"

# (module key, template subdirectory) in render order.
_RENDER_ORDER = (
    ("memory", "memory"),
    ("planning", "planning"),
    ("capability", "capability"),
    ("action", "action"),
)


class CompileError(RuntimeError):
    """Raised when a valid spec cannot be turned into a legal overlay (L2)."""


def _fragment_name(module_key: str, spec_modules: Mapping[str, Any]) -> str | None:
    """Template stem for a module value, or None when it renders nothing."""
    if module_key == "capability":
        mode = spec_modules["capability"]["mode"]
        # 'plus' mounts skills without adding prose; only 'route' has a fragment.
        return "route" if mode == "route" else None
    value = spec_modules[module_key]
    if value == NULL_VALUES[module_key]:
        return None
    return value


def render_appendix(
    spec: Mapping[str, Any],
    templates_root: str | Path = TEMPLATES_ROOT,
) -> tuple[str, list[str]]:
    """Render ``SYSTEM_APPENDIX.md`` text plus the list of fragments used."""
    templates_root = Path(templates_root)
    modules = spec["modules"]

    wanted: list[tuple[str, Path]] = [("preamble", templates_root / "preamble.md")]
    for module_key, subdir in _RENDER_ORDER:
        stem = _fragment_name(module_key, modules)
        if stem is None:
            continue
        wanted.append((f"{subdir}/{stem}", templates_root / subdir / f"{stem}.md"))

    pieces: list[str] = []
    used: list[str] = []
    for label, path in wanted:
        if not path.is_file():
            raise CompileError(f"template fragment missing: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise CompileError(f"template fragment is empty: {path}")
        pieces.append(text)
        used.append(label)

    return FRAGMENT_SEPARATOR.join(pieces) + "\n", used


def compile_spec(
    spec: Mapping[str, Any],
    dest: str | Path,
    *,
    templates_root: str | Path = TEMPLATES_ROOT,
    skills_root: str | Path = SKILLS_ROOT,
    task_skills: Mapping[str, str | Path] | None = None,
    validate: bool = True,
) -> dict:
    """Write the overlay described by ``spec`` into ``dest``.

    ``task_skills`` maps skill name to a source directory, letting a caller
    override or extend the repo-level ``skills/`` bundles with task-local ones.
    Returns an audit record: which fragments were rendered, the appendix size,
    and which skills were copied.
    """
    if validate:
        spec = validate_spec(spec)

    dest = Path(dest)
    if dest.exists():
        if any(dest.iterdir()):
            raise CompileError(f"overlay destination is not empty: {dest}")
    else:
        dest.mkdir(parents=True)

    appendix_text, fragments = render_appendix(spec, templates_root)
    encoded = appendix_text.encode("utf-8")
    if len(encoded) > MAX_APPENDIX_BYTES:
        raise CompileError(
            f"rendered appendix is {len(encoded)} bytes, over the "
            f"{MAX_APPENDIX_BYTES}-byte limit by {len(encoded) - MAX_APPENDIX_BYTES}; "
            f"fragments: {', '.join(fragments)}"
        )
    (dest / "SYSTEM_APPENDIX.md").write_bytes(encoded)

    settings = dict(spec.get("settings") or {})
    if settings:
        (dest / "settings.json").write_text(
            json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    copied = _copy_skills(
        spec["modules"]["capability"]["skills"],
        dest / "skills",
        skills_root=Path(skills_root),
        task_skills=task_skills or {},
    )

    dump_spec(spec, dest / SPEC_FILENAME)

    return {
        "overlay_dir": str(dest),
        "fragments": fragments,
        "appendix_bytes": len(encoded),
        "appendix_budget": MAX_APPENDIX_BYTES,
        "settings_keys": sorted(settings),
        "skills": copied,
    }


def _copy_skills(
    names: list[str],
    dest_skills: Path,
    *,
    skills_root: Path,
    task_skills: Mapping[str, str | Path],
) -> list[str]:
    if not names:
        return []
    dest_skills.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in names:
        source = Path(task_skills.get(name, skills_root / name))
        if not source.is_dir():
            raise CompileError(f"skill {name!r} not found at {source}")
        shutil.copytree(source, dest_skills / name, dirs_exist_ok=False)
        copied.append(name)

    files = [f for f in dest_skills.rglob("*") if f.is_file()]
    if len(files) > MAX_SKILL_FILES:
        raise CompileError(
            f"overlay skills contain {len(files)} files, over the "
            f"{MAX_SKILL_FILES}-file limit; skills: {', '.join(copied)}"
        )
    return copied


def load_seeds(path: str | Path = SEEDS_FILE) -> dict:
    """Named seed combinations (docs/jit-harness-spec.md §4)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def seed_spec(
    name: str,
    task_id: str,
    task_type: str,
    *,
    seeds_path: str | Path = SEEDS_FILE,
) -> dict:
    """Instantiate a named seed as a validated spec for one task."""
    seeds = load_seeds(seeds_path)
    if name not in seeds:
        raise CompileError(
            f"unknown seed {name!r} (available: {', '.join(sorted(seeds))})"
        )
    seed = seeds[name]
    return validate_spec(
        {
            "spec_version": 1,
            "task_id": task_id,
            "task_type": task_type,
            "modules": seed["modules"],
            "settings": seed.get("settings", {}),
            "rationale": seed.get("rationale", ""),
            "reference_ids": [],
            "generated_by": None,
            "seed_name": name,
        }
    )
