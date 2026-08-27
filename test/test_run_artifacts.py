import json
from pathlib import Path

from utils import run_artifacts
from utils.harness import empty_usage
from utils.run_artifacts import (
    atomic_write_json,
    initialize_run_artifacts,
    write_harness_error,
)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_initialize_run_artifacts_creates_complete_placeholders(tmp_path):
    output_dir = tmp_path / "run"

    initialize_run_artifacts(output_dir)

    assert (output_dir / "task_output").is_dir()
    assert (output_dir / "task_output" / "harness").is_dir()
    for name in ("agent.log", "gateway.log", "chat.jsonl"):
        assert (output_dir / name).read_bytes() == b""
    assert read_json(output_dir / "usage.json") == empty_usage()
    assert read_json(output_dir / "score.json") == {"error": "grading_not_run"}


def test_initialize_run_artifacts_is_idempotent_for_completed_artifacts(tmp_path):
    output_dir = tmp_path / "run"
    initialize_run_artifacts(output_dir)
    preserved = {
        "agent.log": b"agent output\n",
        "gateway.log": b"gateway output\n",
        "chat.jsonl": b'{"type":"session"}\n',
    }
    for name, content in preserved.items():
        (output_dir / name).write_bytes(content)
    score = {"overall_score": 0.75}
    usage = empty_usage(2.5)
    usage["request_count"] = 1
    atomic_write_json(output_dir / "score.json", score)
    atomic_write_json(output_dir / "usage.json", usage)

    initialize_run_artifacts(output_dir)

    for name, content in preserved.items():
        assert (output_dir / name).read_bytes() == content
    assert read_json(output_dir / "score.json") == score
    assert read_json(output_dir / "usage.json") == usage


def test_initialize_run_artifacts_repairs_incomplete_json_artifacts(tmp_path):
    output_dir = tmp_path / "run"
    output_dir.mkdir()
    (output_dir / "score.json").write_text("not json", encoding="utf-8")
    (output_dir / "usage.json").write_bytes(b"")

    initialize_run_artifacts(output_dir)

    assert read_json(output_dir / "score.json") == {"error": "grading_not_run"}
    assert read_json(output_dir / "usage.json") == empty_usage()


def test_atomic_write_json_replaces_from_sibling_temp(tmp_path, monkeypatch):
    destination = tmp_path / "artifact.json"
    destination.write_text('{"old": true}', encoding="utf-8")
    original_replace = run_artifacts.os.replace
    observed = {}

    def recording_replace(source, target):
        observed["source"] = Path(source)
        observed["target"] = Path(target)
        observed["bytes"] = Path(source).read_bytes()
        original_replace(source, target)

    monkeypatch.setattr(run_artifacts.os, "replace", recording_replace)

    atomic_write_json(destination, {"new": True})

    assert observed["source"].parent == destination.parent
    assert observed["source"] != destination
    assert observed["target"] == destination
    assert json.loads(observed["bytes"]) == {"new": True}
    assert read_json(destination) == {"new": True}


def test_write_harness_error_uses_harness_directory_and_redacts_secret_fields(tmp_path):
    initialize_run_artifacts(tmp_path)

    path = write_harness_error(
        tmp_path,
        "configuration",
        "provider configuration failed",
        {"api_key": "super-secret", "model": "demo", "nested": {"password": "hidden"}},
    )

    assert path == tmp_path / "task_output" / "harness" / "error.json"
    error = read_json(path)
    assert error == {
        "stage": "configuration",
        "message": "provider configuration failed",
        "details": {
            "api_key": "[REDACTED]",
            "model": "demo",
            "nested": {"password": "[REDACTED]"},
        },
    }
    assert b"super-secret" not in path.read_bytes()
    assert b"hidden" not in path.read_bytes()
