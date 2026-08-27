"""Capability registry ``C_τ``: what a given task can actually do.

The generator (P4.3) cannot pick a ``capability`` module sensibly without
knowing which skills exist for this task, and a spec naming a skill that is not
mounted is the highest-frequency generation error we expect. This module builds
the whitelist that ``harness_spec.validate_spec`` checks against.

Sources, per docs/jit-harness-spec.md §6:

1. the task's own ``## Skills`` section (already parsed by ``parse_task_md``)
2. the repo-level bundles under ``skills/``
3. the task's ``## Env`` section, since a skill whose credentials are missing is
   listed but marked unusable rather than silently offered

Everything here is offline: it reads files and environment variable *names*,
never their values.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = REPO_ROOT / "skills"

# Task-local skill bundles are named after the task (e.g. '03_task1'); they are
# injected by the task itself and are not general-purpose building blocks, so
# they are excluded from the menu offered to a generator.
_TASK_LOCAL_SKILL_RE = re.compile(r"^\d{2}_task\d+$")

MAX_SUMMARY_CHARS = 240


def _split_lines(section: str | None) -> list[str]:
    """Parse a newline-delimited task.md section into a clean list."""
    if not section:
        return []
    seen: list[str] = []
    for line in section.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if item not in seen:
            seen.append(item)
    return seen


def _skill_summary(skill_dir: Path) -> str:
    """One-line capability summary, preferring the SKILL.md frontmatter."""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return ""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    frontmatter = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if frontmatter:
        described = re.search(
            r"^description:\s*(.+)$", frontmatter.group(1), re.MULTILINE
        )
        if described:
            return described.group(1).strip().strip("\"'")[:MAX_SUMMARY_CHARS]
        text = text[frontmatter.end() :]

    for block in text.split("\n\n"):
        block = block.strip()
        if block and not block.startswith(("#", "---", "```")):
            return " ".join(block.split())[:MAX_SUMMARY_CHARS]
    return ""


def discover_skill_bundles(
    skills_root: str | Path = SKILLS_ROOT,
    *,
    include_task_local: bool = False,
) -> dict[str, dict]:
    """Map skill name to ``{path, summary, has_skill_md}`` for repo bundles."""
    skills_root = Path(skills_root)
    if not skills_root.is_dir():
        return {}

    bundles: dict[str, dict] = {}
    for entry in sorted(p for p in skills_root.iterdir() if p.is_dir()):
        if not include_task_local and _TASK_LOCAL_SKILL_RE.match(entry.name):
            continue
        bundles[entry.name] = {
            "path": str(entry),
            "summary": _skill_summary(entry),
            "has_skill_md": (entry / "SKILL.md").is_file(),
        }
    return bundles


def build_registry(
    task: Mapping[str, Any],
    *,
    skills_root: str | Path = SKILLS_ROOT,
    env: Mapping[str, str] | None = None,
) -> dict:
    """Build ``C_τ`` for one parsed task.

    ``mountable`` is the whitelist to hand to ``validate_spec``: repo bundles
    the task does not already carry. Re-mounting a task's own skill through an
    overlay would only shadow it with an identical copy.
    """
    env = os.environ if env is None else env

    task_skills = _split_lines(task.get("skills"))
    env_keys = _split_lines(task.get("env"))
    bundles = discover_skill_bundles(skills_root)

    env_status = {key: bool((env.get(key) or "").strip()) for key in env_keys}
    missing_env = sorted(key for key, present in env_status.items() if not present)

    mountable = sorted(set(bundles) - set(task_skills))

    return {
        "task_id": task.get("task_id"),
        "category": task.get("category"),
        "task_skills": task_skills,
        "mountable": mountable,
        "skills": {
            name: {"summary": info["summary"], "already_mounted": name in task_skills}
            for name, info in bundles.items()
        },
        "env_keys": env_keys,
        "env_present": sorted(key for key, present in env_status.items() if present),
        "env_missing": missing_env,
        "timeout_seconds": task.get("timeout_seconds"),
    }


def available_skills(registry: Mapping[str, Any]) -> list[str]:
    """The whitelist argument for ``validate_spec``."""
    return list(registry["mountable"])


def skill_sources(
    names: Iterable[str],
    registry: Mapping[str, Any],
    *,
    skills_root: str | Path = SKILLS_ROOT,
) -> dict[str, str]:
    """Resolve skill names to source directories for the compiler."""
    skills_root = Path(skills_root)
    resolved: dict[str, str] = {}
    for name in names:
        if name not in registry["skills"]:
            raise KeyError(f"skill {name!r} is not in the capability registry")
        resolved[name] = str(skills_root / name)
    return resolved
