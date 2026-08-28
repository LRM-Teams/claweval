#!/usr/bin/env python
"""Run ClawEval tasks with a JIT-compiled harness appendix (no evolution).

Bridges the WildClawBench JIT harness work (docs/jit-harness-spec.md, P4.1/P4.2)
into the ClawEval suite. It compiles a named seed from the template library into
a SYSTEM_APPENDIX and runs one formal trial per task with that appendix, then
reports the score next to whatever the task's evolution archive already holds.

ClawEval is appendix-only: the F module (mounting skills) and settings.json have
no carrier here, so a seed requesting skills is refused rather than silently
downgraded. Only M/P/A are exercised.

The comparison this is for: a *generic* compiled seed versus the empty appendix
baseline. The evolved champions in the archive have absorbed task-specific
knowledge over many rounds and are not a like-for-like target.

ClawEval lives in a separate repo (agentevals), whose trial runner, env setup and
per-task agent dir this script reuses rather than reimplements. That repo must be
importable, and its own dependencies must be installed, so run this with the
agentevals interpreter:

  ~/agentevals/.venv/bin/python eval/claweval_jit_bridge.py \
    --agentevals-repo ~/agentevals \
    --task-id T014_meeting_notes T026_ambiguous_contact_email \
    --seed tool_grind --port-offset 5000 \
    --output-root ~/WilclawbenchCode/evolved/claweval-jit

Results recorded with this script: docs/jit-harness-claweval-run.md
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import os
import statistics
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# This script ships inside the harness repo, so the template library is local.
DEFAULT_HARNESS_REPO = Path(__file__).resolve().parent.parent
DEFAULT_AGENTEVALS_REPO = Path.home() / "agentevals"
# ClawEval task ids carry no category, so the compiler's category-based default
# cannot fire; every ClawEval task is assigned this type explicitly.
CLAWEVAL_TASK_TYPE = "chat_extract"


def load_claweval_runner(agentevals_repo: Path):
    """Import ClawEval's trial plumbing from the agentevals repo."""
    driver = agentevals_repo / "scripts" / "evolve_claweval_task.py"
    if not driver.is_file():
        raise SystemExit(f"no agentevals checkout at {agentevals_repo}")
    for path in (str(agentevals_repo), str(driver.parent)):
        if path not in sys.path:
            sys.path.insert(0, path)

    # ClawEval spawns its MCP worker as `python -m evals.suites.ClawEval...` and
    # resolves CLAW_EVAL_ROOT relatively, so the whole suite only works with the
    # agentevals checkout as the working directory. Callers must resolve their own
    # paths to absolute before this point.
    if Path.cwd() != agentevals_repo:
        logger.info("chdir to %s (required by the ClawEval suite)", agentevals_repo)
        os.chdir(agentevals_repo)

    module = importlib.import_module("evolve_claweval_task")
    return module.ClawEvalRunner, module.prepare_agent_dir, module.setup_environment


def load_harness_tools(harness_repo: Path):
    """Import this repo's harness compiler under a private package name.

    Both repos have a top-level ``utils`` package, and agentevals' own is a real
    package that wins on sys.path, so the harness modules are bound to
    ``wcb_utils`` instead. Their relative imports resolve inside that alias, and
    the compiler still derives its template paths from its own __file__.
    """
    if not (harness_repo / "utils" / "harness_compile.py").is_file():
        raise SystemExit(f"no JIT harness code at {harness_repo}")

    if "wcb_utils" not in sys.modules:
        package = types.ModuleType("wcb_utils")
        package.__path__ = [str(harness_repo / "utils")]
        sys.modules["wcb_utils"] = package

    module = importlib.import_module("wcb_utils.harness_compile")
    return module.load_seeds, module.render_appendix, module.seed_spec


def build_appendix(
    harness_repo: Path, seed: str, task_id: str, *, appendix_only: bool = True
) -> tuple[str, dict]:
    load_seeds, render_appendix, seed_spec = load_harness_tools(harness_repo)
    seeds = load_seeds()
    if seed not in seeds:
        raise SystemExit(f"unknown seed {seed!r} (available: {', '.join(sorted(seeds))})")

    spec = seed_spec(seed, task_id=task_id, task_type=CLAWEVAL_TASK_TYPE)
    requested = spec["modules"]["capability"]["skills"]
    if requested:
        raise SystemExit(
            f"seed {seed!r} mounts skills {requested}, which ClawEval cannot do "
            "(appendix-only); pick a seed with capability task_only or route"
        )

    downgraded = None
    if appendix_only and spec["modules"]["capability"]["mode"] == "route":
        # ClawEval has no Pi skill registry and excludes write/bash/edit, so the
        # routing fragment would point the agent at tooling that is not there.
        # task_only is the same closed enum, so this stays a legal spec.
        downgraded = "route -> task_only"
        spec["modules"]["capability"] = {"mode": "task_only", "skills": []}
        logger.info("[%s] capability downgraded for appendix-only suite", task_id)

    text, fragments = render_appendix(spec)
    return text.strip(), {
        "seed": seed,
        "modules": spec["modules"],
        "fragments": fragments,
        "appendix_chars": len(text.strip()),
        "capability_downgrade": downgraded,
    }


def summarize(scores: list[float | None]) -> dict:
    """Mean and spread of an arm, keeping failed trials visible as None."""
    graded = [s for s in scores if isinstance(s, (int, float))]
    stats = {
        "trials": len(scores),
        "graded": len(graded),
        "scores": scores,
        "mean": None,
        "stdev": None,
        "min": None,
        "max": None,
    }
    if graded:
        stats["mean"] = round(statistics.fmean(graded), 4)
        stats["min"] = min(graded)
        stats["max"] = max(graded)
        if len(graded) > 1:
            stats["stdev"] = round(statistics.stdev(graded), 4)
    return stats


def archive_reference(archive_root: Path, task_id: str) -> dict:
    """Baseline and champion scores already recorded for this task, if any."""
    graph_file = archive_root / task_id / "graph.json"
    if not graph_file.is_file():
        return {}
    try:
        graph = json.loads(graph_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    nodes = {n.get("candidate_id"): n for n in graph.get("nodes", [])}
    champion_id = graph.get("champion")
    champion = nodes.get(champion_id) or {}

    # The archive's own empty-appendix arms: c0000 is the seeded baseline, but a
    # later candidate can also carry an empty appendix, and c0000 sometimes holds
    # an infrastructure failure rather than a grade.
    archive_empty = []
    for candidate_id, node in nodes.items():
        appendix = (
            archive_root
            / task_id
            / "candidates"
            / candidate_id
            / "overlay"
            / "SYSTEM_APPENDIX.md"
        )
        score = node.get("overall_score")
        if score is None or not appendix.is_file():
            continue
        if not appendix.read_text(encoding="utf-8").strip():
            archive_empty.append({"candidate_id": candidate_id, "score": score})

    return {
        "archive_empty_appendix": archive_empty,
        "champion_candidate": champion_id,
        "champion_score": champion.get("overall_score"),
        "archive_scores": sorted(
            n.get("overall_score")
            for n in nodes.values()
            if n.get("overall_score") is not None
        ),
    }


async def run_task(
    task_id: str,
    appendix: str,
    args: argparse.Namespace,
    port_offset: int,
    label: str = "",
) -> dict:
    task_root = Path(args.output_root).expanduser() / task_id
    task_root.mkdir(parents=True, exist_ok=True)

    ClawEvalRunner, prepare_agent_dir, setup_environment = load_claweval_runner(
        Path(args.agentevals_repo).expanduser()
    )
    setup_environment(task_root / "agent_dir", args.provider, args.model)
    agent_dir = prepare_agent_dir(task_root, args.provider, args.model)

    runner = ClawEvalRunner(
        task_id=task_id,
        model=args.model,
        provider=args.provider,
        agent_dir=agent_dir,
        save_dir=task_root / "runs",
        port_offset=port_offset,
    )
    logger.info(
        "[%s/%s] running trial (port_offset=%d)", task_id, label, port_offset
    )
    evaluation = await runner.evaluate(appendix)
    logger.info(
        "[%s/%s] score=%s feasible=%s error=%s",
        task_id,
        label,
        evaluation.get("score"),
        evaluation.get("feasible"),
        evaluation.get("error"),
    )
    return evaluation


async def run_all(args: argparse.Namespace) -> int:
    # Resolved up front: the ClawEval suite requires a chdir, after which any
    # relative path would point somewhere else.
    harness_repo = Path(args.harness_repo).expanduser().resolve()
    archive_root = Path(args.reference_root).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    args.output_root = str(output_root)
    args.agentevals_repo = str(Path(args.agentevals_repo).expanduser().resolve())
    output_root.mkdir(parents=True, exist_ok=True)

    report = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": args.seed,
        "model": args.model,
        "provider": args.provider,
        "harness_repo": str(harness_repo),
        "suite": "ClawEval",
        "note": "generic compiled seed vs empty-appendix baseline; evolved "
        "champions are task-specific and not a like-for-like target",
        "tasks": {},
    }

    report["num_trials"] = args.num_trials
    report_file = output_root / "jit_report.json"
    port_slot = 0

    for task_id in args.task_ids:
        appendix, audit = build_appendix(
            harness_repo, args.seed, task_id, appendix_only=not args.keep_capability
        )
        logger.info(
            "[%s] appendix: %d chars, fragments=%s",
            task_id,
            audit["appendix_chars"],
            ",".join(audit["fragments"]),
        )
        (output_root / task_id).mkdir(parents=True, exist_ok=True)
        (output_root / task_id / "SYSTEM_APPENDIX.md").write_text(
            appendix + "\n", encoding="utf-8"
        )

        entry = {
            "harness": audit,
            "reference": archive_reference(archive_root, task_id),
            "arms": {},
        }
        # The empty arm is the honest control: same model, same suite, same day,
        # same trial count. The archive's baseline was measured under conditions
        # we cannot reproduce.
        arms = {"empty": "", "jit": appendix}
        if args.jit_only:
            arms.pop("empty")

        for arm_name, arm_appendix in arms.items():
            trials = []
            for trial in range(args.num_trials):
                try:
                    evaluation = await run_task(
                        task_id,
                        arm_appendix,
                        args,
                        args.port_offset + port_slot * 50,
                        label=f"{arm_name}#{trial + 1}",
                    )
                except Exception as exc:  # noqa: BLE001 — keep the batch going
                    logger.error("[%s/%s] trial raised: %s", task_id, arm_name, exc)
                    trials.append({"score": None, "error": str(exc)[:300]})
                else:
                    trials.append(
                        {
                            "score": evaluation.get("score"),
                            "metrics": evaluation.get("metrics"),
                            "status": (evaluation.get("feedback") or {}).get("status"),
                            "error": evaluation.get("error"),
                        }
                    )
                port_slot += 1
                report_file.write_text(
                    json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

            entry["arms"][arm_name] = {
                "trials": trials,
                "summary": summarize([t.get("score") for t in trials]),
            }
            report["tasks"][task_id] = entry
            report_file.write_text(
                json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    print(f"\n=== {args.num_trials} trials per arm, seed={args.seed} ===")
    header = f"{'task':32s} {'empty mean':>12s} {'jit mean':>10s} {'champion':>9s}"
    print(header)
    for task_id, entry in report["tasks"].items():
        arms = entry.get("arms", {})

        def cell(arm: str) -> str:
            summary = (arms.get(arm) or {}).get("summary") or {}
            mean, stdev = summary.get("mean"), summary.get("stdev")
            if mean is None:
                return "n/a"
            return f"{mean}±{stdev}" if stdev is not None else str(mean)

        champion = (entry.get("reference") or {}).get("champion_score")
        print(f"{task_id:32s} {cell('empty'):>12s} {cell('jit'):>10s} "
              f"{str(champion):>9s}")
        for arm in arms:
            print(f"    {arm:6s} {(arms[arm]['summary'])['scores']}")
    print(f"\nreport: {report_file}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--task-id", nargs="+", required=True, dest="task_ids")
    parser.add_argument("--seed", default="tool_grind")
    parser.add_argument("--model", default="deepseek-v4-flash-0731")
    parser.add_argument("--provider", default="zhizengzeng")
    parser.add_argument("--port-offset", type=int, default=5000)
    parser.add_argument(
        "--num-trials",
        type=int,
        default=3,
        help="Trials per arm. Single-trial scores on this suite are noisy enough "
        "to be unreadable (see docs/jit-harness-claweval-run.md)",
    )
    parser.add_argument(
        "--jit-only",
        action="store_true",
        help="Skip the empty-appendix control arm",
    )
    parser.add_argument("--harness-repo", default=str(DEFAULT_HARNESS_REPO))
    parser.add_argument(
        "--agentevals-repo",
        default=str(DEFAULT_AGENTEVALS_REPO),
        help="Checkout providing the ClawEval suite and its trial runner",
    )
    parser.add_argument(
        "--output-root",
        default=str(Path.home() / "WilclawbenchCode" / "evolved" / "claweval-jit"),
    )
    parser.add_argument(
        "--reference-root",
        default=str(
            Path.home()
            / "WilclawbenchCode"
            / "evolved"
            / "claweval-deepseek-v4-flash"
        ),
        help="Existing evolution archive to read baseline/champion scores from",
    )
    parser.add_argument(
        "--keep-capability",
        action="store_true",
        help="Keep a seed's route fragment even though ClawEval has no skill "
        "registry (default: downgrade capability to task_only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compile and print the appendix without running any trial",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        for task_id in args.task_ids:
            appendix, audit = build_appendix(
                Path(args.harness_repo).expanduser(),
                args.seed,
                task_id,
                appendix_only=not args.keep_capability,
            )
            print(f"=== {task_id}: {audit['appendix_chars']} chars, "
                  f"fragments={','.join(audit['fragments'])}")
            print(
                json.dumps(
                    archive_reference(
                        Path(args.reference_root).expanduser(), task_id
                    ),
                    indent=2,
                )
            )
        print(f"\n--- appendix ({args.seed}) ---\n{appendix}")
        return 0

    return asyncio.run(run_all(args))


if __name__ == "__main__":
    raise SystemExit(main())
