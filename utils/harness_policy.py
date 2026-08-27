"""Which tasks are off-limits to harness evolution, and why.

Shared by ``eval/evolve_task.py`` and ``eval/run_batch.py --jit-harness``: any
path that fabricates a harness has to honour the same blacklist, otherwise the
injection tasks can be won by simply telling the agent to distrust the input.

Lives in ``utils/`` rather than beside the evolution script so importing it does
not drag in that script's logging and dotenv side effects.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT_DIR / "configs" / "evolve" / "example.yaml"

# Tasks whose grading measures resistance to adversarial instructions. A harness
# is free prompt real estate, so evolving one for these tasks measures the
# harness author's ingenuity rather than the agent's alignment.
DEFAULT_TASK_BLACKLIST = (
    "06_Safety_Alignment_task_6_prompt_injection",
    "06_Safety_Alignment_task_7_skill_injection",
    "06_Safety_Alignment_task_10_malicious_skill",
)


def load_task_blacklist(config_path: Path = DEFAULT_CONFIG) -> list[str]:
    if config_path.is_file():
        try:
            config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            blacklist = config.get("task_blacklist")
            if isinstance(blacklist, list):
                return [str(item) for item in blacklist]
        except yaml.YAMLError as exc:
            logger.warning("Failed to parse %s: %s", config_path, exc)
    return list(DEFAULT_TASK_BLACKLIST)


def assert_task_allowed(task_id: str, *, flag: str) -> None:
    """Raise if ``task_id`` may not have a harness fabricated for it."""
    if task_id in load_task_blacklist():
        raise ValueError(
            f"task {task_id} is blacklisted for harness evolution; {flag} refused"
        )
