"""Thin evaluation adapter for harness evolution (spec §6.3).

Wraps the existing ``run_single_task`` — no Docker logic is duplicated here.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .evolution_feedback import build_feedback
from .harness_overlay import OverlayValidationError, validate_overlay

ROOT_DIR = Path(__file__).resolve().parent.parent


def _import_run_single_task():
    if str(ROOT_DIR) not in sys.path:
        sys.path.insert(0, str(ROOT_DIR))
    from eval.run_batch import run_single_task

    return run_single_task


class WildClawEvaluationAdapter:
    def __init__(
        self,
        task: dict,
        model: str,
        thinking: str | None = None,
        feedback_mode: str = "soft-metrics",
    ):
        self.task = task
        self.model = model
        self.thinking = thinking
        self.feedback_mode = feedback_mode

    def validate(self, candidate_dir: str | Path | None) -> dict:
        if candidate_dir is None:
            return {"valid": True, "feedback": "baseline (no overlay)"}
        try:
            overlay = validate_overlay(candidate_dir)
        except OverlayValidationError as exc:
            return {"valid": False, "feedback": str(exc)}
        return {"valid": True, "feedback": "", "overlay_dir": str(overlay)}

    def evaluate(self, candidate_dir: str | Path | None = None) -> dict:
        """Run one formal WildClaw evaluation (consumes 1 budget slot)."""
        run_single_task = _import_run_single_task()
        result = run_single_task(
            self.task,
            self.model,
            thinking=self.thinking,
            evolved_harness=str(candidate_dir) if candidate_dir else None,
        )
        output_dir = Path(result["output_dir"])
        feedback = build_feedback(
            output_dir, mode=self.feedback_mode, task_id=self.task["task_id"]
        )
        overall = feedback["overall_score"]
        feasible = overall is not None and not result.get("error")
        return {
            "score": overall,
            "feasible": feasible,
            "metrics": feedback.get("metrics", {}),
            "feedback": feedback,
            "output_dir": str(output_dir),
            "usage": result.get("usage", {}),
            "error": result.get("error"),
        }
