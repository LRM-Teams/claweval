"""Feedback packaging for harness evolution (spec §4).

Only reads run artifacts: score.json, usage.json, task_output/harness/error.json.
Never reads task markdown, Automated Checks, Expected Behavior, or gt/ — the
sanitize guarantee is that nothing outside these run artifacts enters the
feedback bundle. Grading error messages (which may embed answer fragments in
tracebacks) are reduced to a boolean.
"""

from __future__ import annotations

import json
from pathlib import Path

from .run_artifacts import atomic_write_json

FEEDBACK_MODES = ("soft-metrics", "reward_only")

_USAGE_SUMMARY_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "request_count",
    "cost_usd",
)


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def extract_metrics(scores: dict) -> dict[str, float]:
    """Numeric per-check metrics from score.json, excluding the overall score.

    Non-numeric entries (e.g. ``mode`` labels, grading error strings) are
    dropped so they can never leak into feedback.
    """
    metrics = {}
    for key, value in scores.items():
        if key in ("overall_score", "error"):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        metrics[key] = float(value)
    return metrics


def build_feedback(
    output_dir: str | Path,
    mode: str = "soft-metrics",
    task_id: str | None = None,
) -> dict:
    if mode not in FEEDBACK_MODES:
        raise ValueError(f"unknown feedback mode: {mode!r} (allowed: {FEEDBACK_MODES})")

    output_dir = Path(output_dir)
    scores = _read_json(output_dir / "score.json") or {}
    usage = _read_json(output_dir / "usage.json") or {}
    error = _read_json(output_dir / "task_output" / "harness" / "error.json")

    overall = scores.get("overall_score")
    if not isinstance(overall, (int, float)) or isinstance(overall, bool):
        overall = None

    timed_out = bool(error and error.get("message") == "agent timed out")

    feedback: dict = {
        "feedback_mode": mode,
        "task_id": task_id,
        "overall_score": overall,
        "elapsed_sec": usage.get("elapsed_time"),
        "timed_out": timed_out,
        "grading_error": bool(scores.get("error")),
        "trace_ref": "agent.log",
    }
    if error and not timed_out:
        # Stage only; harness error messages are not shown to the evolve agent.
        feedback["harness_error_stage"] = error.get("stage")

    if mode == "soft-metrics":
        feedback["metrics"] = extract_metrics(scores)
        feedback["usage"] = {
            key: usage.get(key) for key in _USAGE_SUMMARY_KEYS if key in usage
        }

    return feedback


def write_feedback(
    output_dir: str | Path,
    destination: str | Path,
    mode: str = "soft-metrics",
    task_id: str | None = None,
) -> dict:
    feedback = build_feedback(output_dir, mode=mode, task_id=task_id)
    atomic_write_json(Path(destination), feedback)
    return feedback
