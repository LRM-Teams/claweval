import json
import os
import tempfile
from pathlib import Path

from .harness import empty_usage


def atomic_write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def initialize_run_artifacts(output_dir):
    output_dir = Path(output_dir)
    task_output = output_dir / "task_output"
    (task_output / "harness").mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("agent.log", "gateway.log", "chat.jsonl"):
        path = output_dir / name
        if not path.exists() or path.stat().st_size == 0:
            path.touch()
    score_path = output_dir / "score.json"
    if not _valid_json(score_path):
        atomic_write_json(score_path, {"error": "grading_not_run"})
    usage_path = output_dir / "usage.json"
    if not _valid_json(usage_path):
        atomic_write_json(usage_path, empty_usage())


def _valid_json(path):
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return True


def _redact(value, key=""):
    sensitive = ("key", "secret", "password", "token", "authorization", "credential")
    if any(word in key.lower() for word in sensitive):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {name: _redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    return value


def write_harness_error(output_dir, stage, message, details=None):
    error = {"stage": stage, "message": message}
    if details is not None:
        error["details"] = _redact(details)
    path = Path(output_dir) / "task_output" / "harness" / "error.json"
    atomic_write_json(path, error)
    return path
