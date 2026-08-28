"""Offline tests for the initial generator and between-round reflector."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils.harness_editor import (  # noqa: E402
    HarnessEditorError,
    build_initial_messages,
    build_reflection_messages,
    propose_initial,
    propose_reflection,
    response_spec,
)


REGISTRY = {
    "task_id": "04_Search_Retrieval_task_4_efficient_search",
    "category": "04_Search_Retrieval",
    "mountable": ["agent-browser"],
    "skills": {
        "agent-browser": {
            "summary": "Headless browser automation",
            "already_mounted": False,
        }
    },
    "env_keys": [],
    "env_present": [],
    "env_missing": [],
}

SPEC = {
    "spec_version": 1,
    "task_id": REGISTRY["task_id"],
    "task_type": "search_budgeted",
    "modules": {
        "memory": "evidence",
        "planning": "checklist",
        "action": "budgeted",
        "capability": {"mode": "task_only", "skills": []},
    },
    "settings": {},
    "rationale": "Track sources and stop after verification.",
    "reference_ids": [],
}


def test_initial_prompt_contains_prompt_but_not_hidden_grading_fields():
    messages = build_initial_messages(
        task_id=SPEC["task_id"],
        task_type=SPEC["task_type"],
        task_prompt="Find the requested fact and cite the source.",
        registry=REGISTRY,
    )
    content = messages[1]["content"]
    assert "Find the requested fact" in content
    assert "Automated Checks" not in content
    assert "ground truth" not in content
    assert "grader rationale" not in content


def test_reflection_prompt_has_sanitized_history_only():
    messages = build_reflection_messages(
        task_id=SPEC["task_id"],
        task_type=SPEC["task_type"],
        task_prompt="Find the requested fact.",
        registry=REGISTRY,
        current_spec=SPEC,
        feedback_history=[
            {
                "round": 1,
                "candidate_id": "c0001",
                "overall_score": 0.5,
                "metrics": {"task_score": 0.5},
                "timed_out": False,
                "trace_tail": "agent attempted a tool call",
            }
        ],
    )
    content = messages[1]["content"]
    assert "0.5" in content
    assert "agent attempted a tool call" in content
    assert "ground truth" not in content.lower()


def test_response_spec_extracts_and_validates_json():
    result = response_spec("Here is the spec:\n" + __import__("json").dumps(SPEC))
    assert result["task_id"] == SPEC["task_id"]


def test_response_spec_rejects_invalid_json():
    with pytest.raises(HarnessEditorError, match="no JSON"):
        response_spec("not a spec")


def test_response_spec_rejects_unknown_skill():
    bad = dict(SPEC)
    bad["modules"] = dict(SPEC["modules"])
    bad["modules"]["capability"] = {
        "mode": "plus",
        "skills": ["not-registered"],
    }
    with pytest.raises(HarnessEditorError, match="not-registered"):
        response_spec(__import__("json").dumps(bad), available_skills=[])


def test_proposals_call_once_and_return_valid_spec():
    calls = []

    def fake_call(messages):
        calls.append(messages)
        return __import__("json").dumps(SPEC)

    assert propose_initial(
        task_id=SPEC["task_id"],
        task_type=SPEC["task_type"],
        task_prompt="Find the requested fact.",
        registry=REGISTRY,
        call=fake_call,
    )["modules"] == SPEC["modules"]
    assert propose_reflection(
        task_id=SPEC["task_id"],
        task_type=SPEC["task_type"],
        task_prompt="Find the requested fact.",
        registry=REGISTRY,
        current_spec=SPEC,
        feedback_history=[{"round": 1, "overall_score": 0.5}],
        call=fake_call,
    )["task_id"] == SPEC["task_id"]
    assert len(calls) == 2


