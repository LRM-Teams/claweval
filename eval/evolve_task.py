"""Per-task harness evolution entry point (spec §5–§6, plan P1).

P1 scope: evaluate the baseline harness, apply one manual (or pre-built)
overlay as a child candidate, evaluate it, and promote the champion. The
automatic budget loop (refine/repair/restart) lands in P2.

Usage:
  python eval/evolve_task.py \
    --task tasks/03_Social_Interaction/03_Social_Interaction_task_1_meeting_negotiation.md \
    --model vllm/gpt-5.5 \
    --feedback-mode soft-metrics \
    --evaluation-budget 2 \
    --manual-overlay /tmp/wcb_overlay
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

from utils.evolution_adapter import WildClawEvaluationAdapter
from utils.evolution_feedback import FEEDBACK_MODES, write_feedback
from utils.harness_overlay import OverlayValidationError, validate_overlay
from utils.harness_policy import load_task_blacklist
from utils.run_artifacts import atomic_write_json
from utils.task_parser import parse_task_md

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
AGENT_LOG_MAX_BYTES = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskArchive:
    """evolved/<task_id>/ layout (spec §3.1), with a minimal graph."""

    def __init__(self, root: Path, task: dict, model: str, feedback_mode: str):
        self.dir = root / task["task_id"]
        self.task = task
        self.candidates_dir = self.dir / "candidates"
        self.graph_path = self.dir / "graph.json"
        self.memory_path = self.dir / "memory.md"
        self._init(model, feedback_mode)
        self.graph = json.loads(self.graph_path.read_text(encoding="utf-8"))

    def _init(self, model: str, feedback_mode: str) -> None:
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "baseline").mkdir(exist_ok=True)
        meta_path = self.dir / "meta.json"
        if not meta_path.exists():
            atomic_write_json(
                meta_path,
                {
                    "task_id": self.task["task_id"],
                    "task_file": self.task["file_path"],
                    "model": model,
                    "feedback_mode": feedback_mode,
                    "created_at": _now(),
                },
            )
        baseline_readme = self.dir / "baseline" / "README.md"
        if not baseline_readme.exists():
            baseline_readme.write_text(
                "Baseline harness = static Pi setup with no overlay "
                "(task skills + image settings as-is).\n"
                f"Task skills:\n{self.task['skills']}\n",
                encoding="utf-8",
            )
        if not self.graph_path.exists():
            atomic_write_json(
                self.graph_path,
                {"task_id": self.task["task_id"], "nodes": [], "edges": [], "champion": None},
            )
        if not self.memory_path.exists():
            self.memory_path.write_text(
                f"# Retrospective memory — {self.task['task_id']}\n\n", encoding="utf-8"
            )

    def save_graph(self) -> None:
        atomic_write_json(self.graph_path, self.graph)

    def append_memory(self, line: str) -> None:
        with self.memory_path.open("a", encoding="utf-8") as handle:
            handle.write(f"- [{_now()}] {line}\n")

    def find_node(self, candidate_id: str) -> dict | None:
        for node in self.graph["nodes"]:
            if node["candidate_id"] == candidate_id:
                return node
        return None

    def evaluated_baseline(self) -> dict | None:
        for node in self.graph["nodes"]:
            if (
                node.get("operator") == "baseline"
                and node.get("status") in ("evaluated", "champion")
                and node.get("overall_score") is not None
            ):
                return node
        return None

    def next_candidate_id(self) -> str:
        existing = [
            int(node["candidate_id"][1:])
            for node in self.graph["nodes"]
            if node["candidate_id"].startswith("c")
            and node["candidate_id"][1:].isdigit()
        ]
        return f"c{(max(existing) + 1 if existing else 0):04d}"

    def candidate_dir(self, candidate_id: str) -> Path:
        return self.candidates_dir / candidate_id

    def add_node(
        self,
        candidate_id: str,
        parent_ids: list[str],
        operator: str,
        model: str,
        feedback_mode: str,
    ) -> dict:
        node = {
            "candidate_id": candidate_id,
            "task_id": self.task["task_id"],
            "parent_ids": parent_ids,
            "reference_ids": [],
            "operator": operator,
            "generation": (
                1 + max(
                    (
                        n.get("generation", 0)
                        for n in self.graph["nodes"]
                        if n["candidate_id"] in parent_ids
                    ),
                    default=-1,
                )
            ),
            "model": model,
            "feedback_mode": feedback_mode,
            "status": "reserved",
            "overall_score": None,
            "metrics": {},
            "created_at": _now(),
        }
        self.graph["nodes"].append(node)
        for parent in parent_ids:
            self.graph["edges"].append(
                {"parent": parent, "child": candidate_id, "operator": operator}
            )
        # manifest.json mirrors the node so a candidate dir is self-describing
        atomic_write_json(self.candidate_dir(candidate_id) / "manifest.json", node)
        self.save_graph()
        return node

    def record_evaluation(self, node: dict, evaluation: dict) -> None:
        node["status"] = "evaluated"
        node["overall_score"] = evaluation["score"]
        node["metrics"] = evaluation.get("metrics", {})
        node["elapsed_sec"] = evaluation.get("feedback", {}).get("elapsed_sec")
        node["output_dir"] = evaluation["output_dir"]
        atomic_write_json(
            self.candidate_dir(node["candidate_id"]) / "manifest.json", node
        )
        self.save_graph()

    def update_champion(self) -> str | None:
        def sort_key(node: dict):
            elapsed = node.get("elapsed_sec")
            return (
                node["overall_score"],
                -(elapsed if isinstance(elapsed, (int, float)) else float("inf")),
            )

        evaluated = [
            node
            for node in self.graph["nodes"]
            if node.get("overall_score") is not None
        ]
        if not evaluated:
            return None
        best = max(evaluated, key=sort_key)
        previous = self.graph.get("champion")
        self.graph["champion"] = best["candidate_id"]
        for node in self.graph["nodes"]:
            if node.get("status") == "champion":
                node["status"] = "evaluated"
        best["status"] = "champion"
        self.save_graph()

        link = self.dir / "champion"
        target = Path("candidates") / best["candidate_id"]
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(target, link)
        if previous != best["candidate_id"]:
            logger.info(
                "[%s] Champion: %s (overall_score=%s)",
                self.task["task_id"],
                best["candidate_id"],
                best["overall_score"],
            )
        return best["candidate_id"]


def collect_candidate_artifacts(
    candidate_dir: Path, output_dir: Path, feedback_mode: str, task_id: str
) -> dict:
    """Copy sanitized run artifacts into candidates/<cid>/eval/<run>/."""
    output_dir = Path(output_dir)
    eval_dir = candidate_dir / "eval" / output_dir.name
    eval_dir.mkdir(parents=True, exist_ok=True)
    for name in ("score.json", "usage.json"):
        source = output_dir / name
        if source.is_file():
            shutil.copy2(source, eval_dir / name)
    agent_log = output_dir / "agent.log"
    if agent_log.is_file():
        data = agent_log.read_bytes()
        if len(data) > AGENT_LOG_MAX_BYTES:
            data = b"[truncated]\n" + data[-AGENT_LOG_MAX_BYTES:]
        (eval_dir / "agent.log").write_bytes(data)
    return write_feedback(
        output_dir, eval_dir / "feedback.json", mode=feedback_mode, task_id=task_id
    )


def default_change_manifest(overlay_dir: Path) -> dict:
    files = sorted(
        f"overlay/{f.relative_to(overlay_dir)}"
        for f in overlay_dir.rglob("*")
        if f.is_file()
    )
    return {
        "changes": [
            {
                "id": "chg-1",
                "description": "manual overlay import (auto-generated manifest)",
                "files": files,
                "target_metrics": [],
                "predicted_effect": "",
                "risk": "",
            }
        ],
        "source": "manual",
    }


def import_overlay_candidate(
    archive: TaskArchive,
    source: Path,
    parent_id: str,
    model: str,
    feedback_mode: str,
) -> tuple[str, dict]:
    source_overlay = validate_overlay(source)
    candidate_id = archive.next_candidate_id()
    candidate_dir = archive.candidate_dir(candidate_id)
    overlay_dest = candidate_dir / "overlay"
    if overlay_dest.exists():
        shutil.rmtree(overlay_dest)
    shutil.copytree(source_overlay, overlay_dest)
    validate_overlay(candidate_dir)

    manifest_source = None
    for location in (source_overlay.parent, source_overlay):
        found = location / "change_manifest.json"
        if found.is_file():
            manifest_source = found
            break
    if manifest_source is not None:
        shutil.copy2(manifest_source, candidate_dir / "change_manifest.json")
    else:
        atomic_write_json(
            candidate_dir / "change_manifest.json",
            default_change_manifest(overlay_dest),
        )

    node = archive.add_node(
        candidate_id,
        parent_ids=[parent_id] if parent_id else [],
        operator="refine",
        model=model,
        feedback_mode=feedback_mode,
    )
    return candidate_id, node


def main() -> None:
    parser = argparse.ArgumentParser(description="Per-task harness evolution")
    parser.add_argument("--task", "-t", required=True, help="Path to task.md")
    parser.add_argument("--model", "-m", required=True, help="Model, e.g. vllm/gpt-5.5")
    parser.add_argument(
        "--feedback-mode", default="soft-metrics", choices=FEEDBACK_MODES
    )
    parser.add_argument(
        "--evaluation-budget",
        type=int,
        default=2,
        help="Formal evaluations allowed, baseline included (default: 2)",
    )
    parser.add_argument("--output-root", default="evolved")
    parser.add_argument(
        "--manual-overlay",
        default=None,
        help="Path to a hand-written overlay dir to evaluate as one child candidate",
    )
    parser.add_argument("--thinking", default=None)
    args = parser.parse_args()

    task_file = Path(args.task)
    if not task_file.exists():
        logger.error("Task file not found: %s", task_file)
        sys.exit(1)
    task = parse_task_md(task_file)
    task_id = task["task_id"]

    if task_id in load_task_blacklist():
        logger.error("Task %s is blacklisted for harness evolution", task_id)
        sys.exit(1)

    output_root = Path(args.output_root).expanduser()
    if not output_root.is_absolute():
        output_root = ROOT_DIR / output_root

    archive = TaskArchive(output_root, task, args.model, args.feedback_mode)
    adapter = WildClawEvaluationAdapter(
        task, args.model, thinking=args.thinking, feedback_mode=args.feedback_mode
    )

    budget = args.evaluation_budget
    used = 0

    # --- baseline (c0000, no overlay) --------------------------------------
    baseline_node = archive.evaluated_baseline()
    if baseline_node is not None:
        logger.info(
            "[%s] Baseline %s already evaluated (overall_score=%s), reusing",
            task_id,
            baseline_node["candidate_id"],
            baseline_node["overall_score"],
        )
    else:
        if used >= budget:
            logger.error("Budget exhausted before baseline evaluation")
            sys.exit(1)
        baseline_id = archive.next_candidate_id()
        (archive.candidate_dir(baseline_id) / "overlay").mkdir(
            parents=True, exist_ok=True
        )
        baseline_node = archive.add_node(
            baseline_id, [], "baseline", args.model, args.feedback_mode
        )
        logger.info("[%s] Evaluating baseline %s ...", task_id, baseline_id)
        evaluation = adapter.evaluate(candidate_dir=None)
        used += 1
        collect_candidate_artifacts(
            archive.candidate_dir(baseline_id),
            Path(evaluation["output_dir"]),
            args.feedback_mode,
            task_id,
        )
        archive.record_evaluation(baseline_node, evaluation)
        archive.append_memory(
            f"{baseline_id} baseline evaluated: overall_score={evaluation['score']}"
        )
        if not evaluation["feasible"]:
            logger.error(
                "[%s] Baseline evaluation infeasible (error=%s); aborting",
                task_id,
                evaluation.get("error"),
            )
            archive.update_champion()
            sys.exit(1)

    # --- one child candidate (P1: manual overlay) --------------------------
    if args.manual_overlay:
        if used >= budget:
            logger.error("Budget exhausted; skip child evaluation")
            sys.exit(1)
        try:
            child_id, child_node = import_overlay_candidate(
                archive,
                Path(args.manual_overlay),
                baseline_node["candidate_id"],
                args.model,
                args.feedback_mode,
            )
        except OverlayValidationError as exc:
            logger.error("Manual overlay rejected: %s", exc)
            sys.exit(1)
        logger.info("[%s] Evaluating child %s ...", task_id, child_id)
        evaluation = adapter.evaluate(candidate_dir=archive.candidate_dir(child_id))
        used += 1
        collect_candidate_artifacts(
            archive.candidate_dir(child_id),
            Path(evaluation["output_dir"]),
            args.feedback_mode,
            task_id,
        )
        archive.record_evaluation(child_node, evaluation)
        archive.append_memory(
            f"{child_id} (parent={baseline_node['candidate_id']}, manual overlay) "
            f"evaluated: overall_score={evaluation['score']}"
        )
    else:
        logger.info(
            "[%s] No --manual-overlay given; baseline-only run (P2 adds the auto loop)",
            task_id,
        )

    champion = archive.update_champion()
    logger.info(
        "[%s] Done. budget_used=%d/%d champion=%s archive=%s",
        task_id,
        used,
        budget,
        champion,
        archive.dir,
    )


if __name__ == "__main__":
    main()
