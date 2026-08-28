"""Host-side initial harness generator and between-round reflector.

The editor is deliberately outside the Pi container. It may see the task
prompt, the current declarative harness, and the sanitized evaluation feedback,
but it never receives Automated Checks, grading code, ground truth, or grader
rationale. It returns another ``harness.spec.json`` object, not free-form
overlay files; schema/compile validation remains the authority.

The API is OpenAI-compatible because the repository already uses remote model
endpoints for evaluation. No API call is made at import time.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Mapping

import requests

from .harness_spec import SPEC_VERSION, validate_spec

LLMCall = Callable[[list[dict[str, str]]], str]


class HarnessEditorError(RuntimeError):
    """Raised when an editor response cannot produce a valid spec."""


EDITOR_SYSTEM = """You are a harness editor for a fixed Pi ReAct agent.
Design or revise only the agent harness, not the task solution.

Hard rules:
- You may use the task prompt, but you must not ask for or infer hidden
  Automated Checks, ground truth, grader rationale, or the reference answer.
- You receive evaluation feedback and an optional agent behavior trace only.
  Do not claim to know why a hidden grader marked something right or wrong.
- Return exactly one JSON object and no markdown. It must contain:
  spec_version, task_id, task_type, modules, settings, rationale,
  reference_ids.
- modules must use only the closed M/P/A/F enums. Do not invent a module,
  setting, skill, file, or capability.
- Keep rationale short and describe a falsifiable harness hypothesis, not an
  answer to the task.
- Never add instructions about trusting, ignoring, or bypassing user
  instructions. Safety behavior is not a tunable harness surface.
"""


def build_initial_messages(
    *,
    task_id: str,
    task_type: str,
    task_prompt: str,
    registry: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Prompt for the first task-specific harness before Round 1."""
    user = {
        "operation": "initial_harness",
        "task_id": task_id,
        "task_type": task_type,
        "task_prompt": task_prompt,
        "capability_registry": registry,
        "evaluation_feedback": None,
        "current_spec": None,
    }
    return [
        {"role": "system", "content": EDITOR_SYSTEM},
        {
            "role": "user",
            "content": (
                "Choose the smallest closed-enum harness likely to make this "
                "task reliable. The task prompt is the only task-specific "
                "source you may use.\n"
                + json.dumps(user, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def build_reflection_messages(
    *,
    task_id: str,
    task_type: str,
    task_prompt: str,
    registry: Mapping[str, Any],
    current_spec: Mapping[str, Any],
    feedback_history: list[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Prompt for a between-round revision.

    ``feedback_history`` should contain the sanitized feedback returned by
    ``utils.evolution_feedback.build_feedback`` plus an optional bounded
    ``trace_tail`` behavior excerpt. It intentionally does not accept a raw
    evaluation object or task output directory.
    """
    user = {
        "operation": "reflect_harness",
        "task_id": task_id,
        "task_type": task_type,
        "task_prompt": task_prompt,
        "capability_registry": registry,
        "current_spec": dict(current_spec),
        "feedback_history": [dict(item) for item in feedback_history],
    }
    return [
        {"role": "system", "content": EDITOR_SYSTEM},
        {
            "role": "user",
            "content": (
                "Revise the current harness only when the hidden outcome "
                "provides evidence for a change. Preserve useful settings and "
                "skills. Return a complete replacement spec, not a patch.\n"
                + json.dumps(user, ensure_ascii=False, sort_keys=True)
            ),
        },
    ]


def response_spec(text: str, *, available_skills: list[str] | None = None) -> dict:
    """Extract exactly one JSON spec and validate it offline."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise HarnessEditorError("editor response contains no JSON object")
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise HarnessEditorError(f"editor response is not valid JSON: {exc}") from exc
    try:
        return validate_spec(raw, available_skills=available_skills)
    except ValueError as exc:
        raise HarnessEditorError(f"editor returned invalid harness spec: {exc}") from exc


def openai_call(
    messages: list[dict[str, str]],
    *,
    model: str,
    api_key: str,
    base_url: str = "https://api.openai.com/v1",
    timeout: float = 120.0,
) -> str:
    """Call an OpenAI-compatible chat endpoint without logging credentials."""
    if not api_key:
        raise HarnessEditorError("editor API key is empty")
    endpoint = base_url.rstrip("/") + "/chat/completions"
    try:
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": messages, "temperature": 0.2},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload["choices"][0]["message"]["content"] or "")
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        raise HarnessEditorError(f"editor request failed: {exc}") from exc


def make_editor_call(
    *,
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
) -> LLMCall:
    """Create a configured editor callback from explicit args or env defaults."""
    api_key = api_key or os.environ.get("JIT_EDITOR_API_KEY") or os.environ.get(
        "OPENAI_API_KEY", ""
    )
    base_url = base_url or os.environ.get(
        "JIT_EDITOR_BASE_URL", "https://api.openai.com/v1"
    )
    return lambda messages: openai_call(
        messages, model=model, api_key=api_key, base_url=base_url
    )


def propose_initial(
    *,
    task_id: str,
    task_type: str,
    task_prompt: str,
    registry: Mapping[str, Any],
    call: LLMCall,
) -> dict:
    return response_spec(
        call(
            build_initial_messages(
                task_id=task_id,
                task_type=task_type,
                task_prompt=task_prompt,
                registry=registry,
            )
        ),
        available_skills=list(registry.get("mountable", [])),
    )


def propose_reflection(
    *,
    task_id: str,
    task_type: str,
    task_prompt: str,
    registry: Mapping[str, Any],
    current_spec: Mapping[str, Any],
    feedback_history: list[Mapping[str, Any]],
    call: LLMCall,
) -> dict:
    return response_spec(
        call(
            build_reflection_messages(
                task_id=task_id,
                task_type=task_type,
                task_prompt=task_prompt,
                registry=registry,
                current_spec=current_spec,
                feedback_history=feedback_history,
            )
        ),
        available_skills=list(registry.get("mountable", [])),
    )
