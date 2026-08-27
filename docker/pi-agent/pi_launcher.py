#!/usr/bin/env python3

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def terminate_group(process, grace_seconds):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--agent-dir", required=True)
    parser.add_argument("--grace-seconds", type=float, default=5.0)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")

    control_dir = Path(args.agent_dir).parent / "control"
    control_dir.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(command, start_new_session=True)
    (control_dir / "process.json").write_text(
        json.dumps({"pid": process.pid, "pgid": process.pid}) + "\n",
        encoding="utf-8",
    )

    timed_out = False
    try:
        return_code = process.wait(timeout=args.timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        terminate_group(process, args.grace_seconds)
        return_code = 124
    finally:
        if process.poll() is None:
            terminate_group(process, args.grace_seconds)
        (control_dir / "result.json").write_text(
            json.dumps({"returncode": return_code, "timed_out": timed_out}) + "\n",
            encoding="utf-8",
        )
    return return_code


if __name__ == "__main__":
    sys.exit(main())
