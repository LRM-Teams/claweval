"""``harness.spec.json`` schema: the closed design space of Pi harnesses.

A spec is the *specification* of an overlay, not the overlay itself. Everything
under ``overlay/`` except ``harness.spec.json`` is compiled from it by
``utils.harness_compile``, so a spec plus the template library reproduces an
overlay byte for byte.

The four modules ``(M, P, A, F)`` follow docs/jit-harness-spec.md §2. Only ``F``
is a hard capability change (mounting skills); ``M``/``P``/``A`` are
protocol-level instantiations rendered into ``SYSTEM_APPENDIX.md`` and therefore
only as strong as the model's adherence to them.

``validate_spec`` is the offline part of the paper's ``Valid_Π`` (spec §8, L1).
It never starts a container and never calls an API.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

SPEC_VERSION = 1
SPEC_FILENAME = "harness.spec.json"

MEMORY_VALUES = ("full", "notes", "resum", "evidence")
PLANNING_VALUES = ("none", "checklist", "dag", "decomp")
ACTION_VALUES = ("react", "persistent", "verify", "budgeted")
CAPABILITY_MODES = ("task_only", "plus", "route")

# Values that render no fragment (the runtime default for that module).
NULL_VALUES = {"memory": "full", "planning": "none", "action": "react"}

TASK_TYPES = (
    "search_budgeted",
    "search_deep",
    "code_repo",
    "code_puzzle",
    "chat_extract",
    "creative_media",
    "productivity_crawl",
)

SETTINGS_ALLOWLIST = ("thinkingDefault",)
THINKING_VALUES = ("low", "medium", "high")

MAX_RATIONALE_CHARS = 600
_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class SpecValidationError(ValueError):
    """Raised for any spec that is outside the declared design space."""


def _require_choice(value: Any, allowed: Iterable[str], field: str) -> str:
    allowed = tuple(allowed)
    if value not in allowed:
        raise SpecValidationError(
            f"{field}={value!r} is not a legal value (allowed: {', '.join(allowed)})"
        )
    return value


def validate_spec(
    spec: Mapping[str, Any],
    *,
    available_skills: Iterable[str] | None = None,
) -> dict:
    """Validate a spec and return it normalised.

    ``available_skills`` is the capability registry ``C_τ``. When given, every
    requested skill must appear in it; a spec naming a skill that does not exist
    for this task is the highest-frequency generator error we expect.
    """
    if not isinstance(spec, Mapping):
        raise SpecValidationError("spec must be a JSON object")

    version = spec.get("spec_version")
    if version != SPEC_VERSION:
        raise SpecValidationError(
            f"spec_version={version!r} unsupported (expected {SPEC_VERSION})"
        )

    task_id = spec.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise SpecValidationError("task_id must be a non-empty string")

    task_type = _require_choice(spec.get("task_type"), TASK_TYPES, "task_type")

    modules = spec.get("modules")
    if not isinstance(modules, Mapping):
        raise SpecValidationError("modules must be a JSON object")
    unknown = sorted(
        set(modules) - {"memory", "planning", "action", "capability"}
    )
    if unknown:
        raise SpecValidationError(f"unknown module keys: {', '.join(unknown)}")

    memory = _require_choice(modules.get("memory"), MEMORY_VALUES, "modules.memory")
    planning = _require_choice(
        modules.get("planning"), PLANNING_VALUES, "modules.planning"
    )
    action = _require_choice(modules.get("action"), ACTION_VALUES, "modules.action")
    capability = _validate_capability(
        modules.get("capability"), available_skills=available_skills
    )

    settings = _validate_settings(spec.get("settings"))

    rationale = spec.get("rationale", "")
    if not isinstance(rationale, str):
        raise SpecValidationError("rationale must be a string")
    if len(rationale) > MAX_RATIONALE_CHARS:
        raise SpecValidationError(
            f"rationale is {len(rationale)} chars (limit {MAX_RATIONALE_CHARS})"
        )

    reference_ids = spec.get("reference_ids", [])
    if not isinstance(reference_ids, list) or not all(
        isinstance(r, str) for r in reference_ids
    ):
        raise SpecValidationError("reference_ids must be a list of strings")

    normalised = {
        "spec_version": SPEC_VERSION,
        "task_id": task_id,
        "task_type": task_type,
        "modules": {
            "memory": memory,
            "planning": planning,
            "action": action,
            "capability": capability,
        },
        "settings": settings,
        "rationale": rationale,
        "reference_ids": list(reference_ids),
    }
    for optional in ("generated_by", "created_at", "seed_name"):
        if optional in spec:
            value = spec[optional]
            if value is not None and not isinstance(value, str):
                raise SpecValidationError(f"{optional} must be a string or null")
            normalised[optional] = value
    return normalised


def _validate_capability(
    capability: Any,
    *,
    available_skills: Iterable[str] | None,
) -> dict:
    if not isinstance(capability, Mapping):
        raise SpecValidationError("modules.capability must be a JSON object")
    unknown = sorted(set(capability) - {"mode", "skills"})
    if unknown:
        raise SpecValidationError(
            f"unknown modules.capability keys: {', '.join(unknown)}"
        )

    mode = _require_choice(
        capability.get("mode"), CAPABILITY_MODES, "modules.capability.mode"
    )
    skills = capability.get("skills", [])
    if not isinstance(skills, list) or not all(isinstance(s, str) for s in skills):
        raise SpecValidationError("modules.capability.skills must be a list of strings")

    if mode == "task_only" and skills:
        raise SpecValidationError(
            "modules.capability.mode='task_only' cannot request skills"
        )
    if mode == "plus" and not skills:
        raise SpecValidationError(
            "modules.capability.mode='plus' requires at least one skill"
        )

    for name in skills:
        if not _SKILL_NAME_RE.match(name):
            raise SpecValidationError(f"illegal skill name: {name!r}")

    if available_skills is not None:
        registry = set(available_skills)
        missing = sorted(set(skills) - registry)
        if missing:
            raise SpecValidationError(
                f"skills not in capability registry: {', '.join(missing)} "
                f"(available: {', '.join(sorted(registry)) or 'none'})"
            )

    # Deduplicate while keeping declaration order so compilation is deterministic.
    seen: list[str] = []
    for name in skills:
        if name not in seen:
            seen.append(name)
    return {"mode": mode, "skills": seen}


def _validate_settings(settings: Any) -> dict:
    if settings is None:
        return {}
    if not isinstance(settings, Mapping):
        raise SpecValidationError("settings must be a JSON object")
    rejected = sorted(key for key in settings if key not in SETTINGS_ALLOWLIST)
    if rejected:
        raise SpecValidationError(
            f"settings keys not in allowlist: {', '.join(rejected)} "
            f"(allowed: {', '.join(SETTINGS_ALLOWLIST)})"
        )
    if "thinkingDefault" in settings:
        _require_choice(
            settings["thinkingDefault"], THINKING_VALUES, "settings.thinkingDefault"
        )
    return dict(settings)


def load_spec(path: str | Path, **kwargs: Any) -> dict:
    """Read and validate a spec file."""
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpecValidationError(f"cannot read spec {path}: {exc}") from exc
    return validate_spec(raw, **kwargs)


def dump_spec(spec: Mapping[str, Any], path: str | Path) -> Path:
    """Write a spec with stable formatting so specs diff cleanly."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def module_signature(spec: Mapping[str, Any]) -> str:
    """Compact ``M/P/A/F`` identity, used as a bank dedup key."""
    modules = spec["modules"]
    capability = modules["capability"]
    tail = capability["mode"]
    if capability["skills"]:
        tail = f"{tail}:{'+'.join(capability['skills'])}"
    return "/".join(
        (modules["memory"], modules["planning"], modules["action"], tail)
    )
