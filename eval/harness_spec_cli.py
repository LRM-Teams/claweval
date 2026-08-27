#!/usr/bin/env python3
"""Offline harness-spec tool: list seeds, compile a spec or seed to an overlay.

Everything this does is L1-L3 (schema, compile, overlay) from
docs/jit-harness-spec.md §8 -- no container, no API call.

    python3 eval/harness_spec_cli.py seeds
    python3 eval/harness_spec_cli.py show tool_grind
    python3 eval/harness_spec_cli.py compile --seed tool_grind \
        --task-id 03_Chat_Tool_task_1_meeting --task-type productivity_crawl \
        --dest /tmp/overlay
    python3 eval/harness_spec_cli.py compile --spec path/to/harness.spec.json \
        --dest /tmp/overlay
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.harness_compile import (  # noqa: E402
    CompileError,
    compile_spec,
    load_seeds,
    render_appendix,
    seed_spec,
)
from utils.harness_overlay import OverlayValidationError, validate_overlay
from utils.harness_spec import SpecValidationError, load_spec, module_signature


def _resolve_spec(args: argparse.Namespace) -> dict:
    if args.seed:
        if not (args.task_id and args.task_type):
            raise SystemExit("--seed requires --task-id and --task-type")
        return seed_spec(args.seed, task_id=args.task_id, task_type=args.task_type)
    return load_spec(args.spec)


def cmd_seeds(_args: argparse.Namespace) -> int:
    for name, seed in sorted(load_seeds().items()):
        spec = seed_spec(name, task_id="_", task_type="code_repo")
        print(f"{name:16s} {module_signature(spec):40s} {seed['paper']}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    spec = _resolve_spec(args)
    text, fragments = render_appendix(spec)
    print(json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True))
    print(f"\n--- fragments: {', '.join(fragments)}")
    print(f"--- appendix: {len(text.encode('utf-8'))} bytes\n")
    print(text, end="")
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    spec = _resolve_spec(args)
    audit = compile_spec(spec, args.dest)
    validate_overlay(args.dest)
    print(json.dumps(audit, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("seeds", help="list the named seed combinations").set_defaults(
        func=cmd_seeds
    )

    for name, func, help_text in (
        ("show", cmd_show, "print a spec and its rendered appendix"),
        ("compile", cmd_compile, "compile a spec into an overlay directory"),
    ):
        p = sub.add_parser(name, help=help_text)
        source = p.add_mutually_exclusive_group(required=True)
        source.add_argument("--seed", help="named seed to instantiate")
        source.add_argument("--spec", type=Path, help="path to a harness.spec.json")
        p.add_argument("--task-id", help="task id (required with --seed)")
        p.add_argument("--task-type", help="task type (required with --seed)")
        if name == "compile":
            p.add_argument(
                "--dest", type=Path, required=True, help="empty overlay destination"
            )
        p.set_defaults(func=func)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (SpecValidationError, CompileError, OverlayValidationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
