"""Offline controller test for three-round JIT evolution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from eval import evolve_task  # noqa: E402
from utils.harness_spec import validate_spec  # noqa: E402


def test_three_rounds_create_an_immutable_parent_chain(monkeypatch, tmp_path):
    task = {
        "task_id": "02_Code_Intelligence_task_1_demo",
        "category": "02_Code_Intelligence",
        "file_path": str(tmp_path / "task.md"),
        "prompt": "Inspect the repository and produce the requested result.",
        "skills": "",
        "env": "",
        "timeout_seconds": 60,
    }
    archive = evolve_task.TaskArchive(
        tmp_path / "archive", task, "editor-model", "soft-metrics"
    )

    spec = validate_spec(
        {
            "spec_version": 1,
            "task_id": task["task_id"],
            "task_type": "code_repo",
            "modules": {
                "memory": "notes",
                "planning": "checklist",
                "action": "verify",
                "capability": {"mode": "task_only", "skills": []},
            },
            "settings": {},
            "rationale": "Keep a short factual record and verify artifacts.",
            "reference_ids": [],
        }
    )
    editor_calls = []

    def fake_editor_call(**_kwargs):
        def call(messages):
            editor_calls.append(messages)
            return json.dumps(spec)

        return call

    monkeypatch.setattr(evolve_task, "make_editor_call", fake_editor_call)
    scores = iter((0.2, 0.8, 0.5))

    class FakeAdapter:
        feedback_mode = "soft-metrics"

        def evaluate(self, candidate_dir):
            score = next(scores)
            return {
                "score": score,
                "feasible": True,
                "metrics": {"task_score": score},
                "feedback": {
                    "overall_score": score,
                    "metrics": {"task_score": score},
                    "elapsed_sec": 1,
                },
                "output_dir": str(tmp_path / f"run-{score}"),
                "error": None,
            }

    def fake_collect(_candidate_dir, _output_dir, _mode, _task_id):
        # This is the only data made visible to the reflector.
        return {
            "overall_score": 0.2,
            "metrics": {},
            "elapsed_sec": 1,
            "timed_out": False,
            "grading_error": False,
        }

    monkeypatch.setattr(evolve_task, "collect_candidate_artifacts", fake_collect)

    champion = evolve_task.run_jit_evolution(
        task,
        archive,
        FakeAdapter(),
        rounds=3,
        editor_model="editor-model",
        editor_api_key="test-key",
        editor_base_url="http://unused",
    )

    assert champion == "c0001"
    assert len(editor_calls) == 3
    assert [node["candidate_id"] for node in archive.graph["nodes"]] == [
        "c0000",
        "c0001",
        "c0002",
    ]
    assert archive.graph["edges"] == [
        {"parent": "c0000", "child": "c0001", "operator": "jit_reflect"},
        {"parent": "c0001", "child": "c0002", "operator": "jit_reflect"},
    ]
    assert archive.find_node("c0001")["overall_score"] == 0.8
    assert archive.find_node("c0002")["overall_score"] == 0.5
    assert (archive.candidate_dir("c0000") / "overlay" / "harness.spec.json").is_file()
