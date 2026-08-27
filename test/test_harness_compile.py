"""P4.1 acceptance: schema, template library, and compiler.

Everything here is offline: no container, no API call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils.harness_compile import (  # noqa: E402
    CompileError,
    compile_spec,
    load_seeds,
    render_appendix,
    seed_spec,
)
from utils.harness_overlay import (  # noqa: E402
    MAX_APPENDIX_BYTES,
    OverlayValidationError,
    validate_overlay,
)
from utils.harness_spec import (  # noqa: E402
    ACTION_VALUES,
    MEMORY_VALUES,
    PLANNING_VALUES,
    SPEC_FILENAME,
    SpecValidationError,
    module_signature,
    validate_spec,
)

HAND_WRITTEN_TOOL_GRIND = (
    REPO_ROOT / "configs" / "evolve" / "seeds" / "tool_grind" / "SYSTEM_APPENDIX.md"
)
SEED_NAMES = (
    "react",
    "plan_execute",
    "resum",
    "flash_dag",
    "evidence_search",
    "tool_grind",
)


def base_spec(**overrides):
    spec = {
        "spec_version": 1,
        "task_id": "04_Search_Retrieval_task_4_efficient_search",
        "task_type": "search_budgeted",
        "modules": {
            "memory": "evidence",
            "planning": "checklist",
            "action": "budgeted",
            "capability": {"mode": "task_only", "skills": []},
        },
        "settings": {"thinkingDefault": "high"},
        "rationale": "capped searches; answers must be traceable",
        "reference_ids": [],
    }
    spec.update(overrides)
    return spec


# --- schema (L1) -----------------------------------------------------------


def test_valid_spec_normalises():
    spec = validate_spec(base_spec())
    assert spec["modules"]["capability"] == {"mode": "task_only", "skills": []}
    assert module_signature(spec) == "evidence/checklist/budgeted/task_only"


@pytest.mark.parametrize(
    "field,value",
    [
        ("spec_version", 2),
        ("task_id", ""),
        ("task_type", "not_a_type"),
    ],
)
def test_rejects_bad_scalar_fields(field, value):
    with pytest.raises(SpecValidationError):
        validate_spec(base_spec(**{field: value}))


@pytest.mark.parametrize("module", ["memory", "planning", "action"])
def test_rejects_module_value_outside_enum(module):
    spec = base_spec()
    spec["modules"][module] = "freeform"
    with pytest.raises(SpecValidationError, match=module):
        validate_spec(spec)


def test_rejects_unknown_module_key():
    spec = base_spec()
    spec["modules"]["reflection"] = "on"
    with pytest.raises(SpecValidationError, match="reflection"):
        validate_spec(spec)


def test_rejects_settings_outside_allowlist():
    with pytest.raises(SpecValidationError, match="model"):
        validate_spec(base_spec(settings={"model": "gpt-5.5"}))


def test_task_only_cannot_request_skills():
    spec = base_spec()
    spec["modules"]["capability"] = {"mode": "task_only", "skills": ["agent-browser"]}
    with pytest.raises(SpecValidationError, match="task_only"):
        validate_spec(spec)


def test_plus_requires_at_least_one_skill():
    spec = base_spec()
    spec["modules"]["capability"] = {"mode": "plus", "skills": []}
    with pytest.raises(SpecValidationError, match="plus"):
        validate_spec(spec)


def test_skill_must_exist_in_capability_registry():
    """The error the generator is expected to make most often."""
    spec = base_spec()
    spec["modules"]["capability"] = {"mode": "plus", "skills": ["does-not-exist"]}
    with pytest.raises(SpecValidationError, match="does-not-exist"):
        validate_spec(spec, available_skills=["agent-browser", "video-frames"])


def test_skills_are_deduplicated_in_order():
    spec = base_spec()
    spec["modules"]["capability"] = {
        "mode": "plus",
        "skills": ["video-frames", "agent-browser", "video-frames"],
    }
    validated = validate_spec(spec, available_skills=["agent-browser", "video-frames"])
    assert validated["modules"]["capability"]["skills"] == [
        "video-frames",
        "agent-browser",
    ]


# --- rendering and compilation (L2) ---------------------------------------


def test_null_values_render_no_fragment():
    spec = validate_spec(
        base_spec(
            modules={
                "memory": "full",
                "planning": "none",
                "action": "react",
                "capability": {"mode": "task_only", "skills": []},
            }
        )
    )
    _, fragments = render_appendix(spec)
    assert fragments == ["preamble"]


def test_fragment_order_follows_m_p_f_a():
    spec = validate_spec(
        base_spec(
            modules={
                "memory": "notes",
                "planning": "checklist",
                "action": "persistent",
                "capability": {"mode": "route", "skills": []},
            }
        )
    )
    _, fragments = render_appendix(spec)
    assert fragments == [
        "preamble",
        "memory/notes",
        "planning/checklist",
        "capability/route",
        "action/persistent",
    ]


def test_compile_is_deterministic(tmp_path):
    spec = validate_spec(base_spec())
    first = compile_spec(spec, tmp_path / "a")
    second = compile_spec(spec, tmp_path / "b")
    assert first["fragments"] == second["fragments"]
    for name in ("SYSTEM_APPENDIX.md", "settings.json", SPEC_FILENAME):
        assert (tmp_path / "a" / name).read_bytes() == (
            tmp_path / "b" / name
        ).read_bytes()


def test_compile_refuses_nonempty_destination(tmp_path):
    dest = tmp_path / "overlay"
    dest.mkdir()
    (dest / "stray.txt").write_text("x")
    with pytest.raises(CompileError, match="not empty"):
        compile_spec(validate_spec(base_spec()), dest)


def test_compile_reports_missing_skill_source(tmp_path):
    spec = base_spec()
    spec["modules"]["capability"] = {"mode": "plus", "skills": ["ghost"]}
    with pytest.raises(CompileError, match="ghost"):
        compile_spec(
            validate_spec(spec), tmp_path / "overlay", skills_root=tmp_path / "skills"
        )


def test_compile_mounts_skill_and_stays_consistent(tmp_path):
    skills_root = tmp_path / "skills"
    (skills_root / "fake-skill").mkdir(parents=True)
    (skills_root / "fake-skill" / "SKILL.md").write_text("# fake\n")

    spec = base_spec()
    spec["modules"]["capability"] = {"mode": "plus", "skills": ["fake-skill"]}
    dest = tmp_path / "overlay"
    audit = compile_spec(
        validate_spec(spec, available_skills=["fake-skill"]),
        dest,
        skills_root=skills_root,
    )
    assert audit["skills"] == ["fake-skill"]
    assert (dest / "skills" / "fake-skill" / "SKILL.md").is_file()
    validate_overlay(dest)


def test_oversized_appendix_errors_instead_of_truncating(tmp_path):
    templates = tmp_path / "templates"
    (templates / "memory").mkdir(parents=True)
    (templates / "preamble.md").write_text("p\n")
    (templates / "memory" / "notes.md").write_text("x" * (MAX_APPENDIX_BYTES + 10))

    spec = base_spec()
    spec["modules"] = {
        "memory": "notes",
        "planning": "none",
        "action": "react",
        "capability": {"mode": "task_only", "skills": []},
    }
    with pytest.raises(CompileError, match="over the"):
        compile_spec(
            validate_spec(spec), tmp_path / "overlay", templates_root=templates
        )


# --- named seeds (L3) -----------------------------------------------------


def test_seed_file_holds_exactly_the_documented_six():
    assert sorted(load_seeds()) == sorted(SEED_NAMES)


@pytest.mark.parametrize("name", SEED_NAMES)
def test_every_seed_compiles_to_a_legal_overlay(name, tmp_path):
    spec = seed_spec(name, task_id="99_demo_task", task_type="code_repo")
    dest = tmp_path / name
    audit = compile_spec(spec, dest)
    assert audit["appendix_bytes"] <= MAX_APPENDIX_BYTES
    assert validate_overlay(dest) == dest


def test_unknown_seed_name_is_rejected():
    with pytest.raises(CompileError, match="unknown seed"):
        seed_spec("no_such_seed", task_id="t", task_type="code_repo")


def test_react_seed_is_the_minimal_fallback():
    spec = seed_spec("react", task_id="t", task_type="code_repo")
    _, fragments = render_appendix(spec)
    assert fragments == ["preamble"]


def test_no_template_mentions_instruction_trust():
    """Risk table item 1: the library must not let a harness reason about
    whether to obey instructions, or 06_Safety_Alignment becomes vacuous."""
    banned = ("ignore", "suspicious", "untrusted", "prompt injection", "disregard")
    offenders = []
    for path in (REPO_ROOT / "harness" / "templates").rglob("*.md"):
        text = path.read_text(encoding="utf-8").lower()
        for word in banned:
            if word in text:
                offenders.append(f"{path.name}: {word}")
    assert not offenders, offenders


# --- tool_grind equivalence (the P4.1 exit criterion) ---------------------


def _rules(text: str) -> str:
    return " ".join(text.lower().split())


@pytest.mark.parametrize(
    "requirement",
    [
        # 1. never trust the real-world clock
        ["never trust the real-world clock", "sweep adjacent"],
        # 2. empty/failed result is not absence; retry with varied parameters
        ["retry with systematically varied parameters", "never abandon a tool"],
        # 3. never guess a retrieved value
        ["never guess a value you were asked to retrieve"],
        # 4. enumerate every required subtask and keep a checklist
        ["enumerate every required subtask", "checklist"],
        # 5. use the available time
        ["use the available time", "finishing early"],
    ],
)
def test_tool_grind_compiles_to_the_hand_written_rules(requirement, tmp_path):
    spec = seed_spec(
        "tool_grind",
        task_id="03_Chat_Tool_task_1_meeting",
        task_type="productivity_crawl",
    )
    text, _ = render_appendix(spec)
    compiled = _rules(text)
    original = _rules(HAND_WRITTEN_TOOL_GRIND.read_text(encoding="utf-8"))
    for phrase in requirement:
        assert phrase in original, f"phrase drifted out of the hand-written seed: {phrase}"
        assert phrase in compiled, f"compiled tool_grind lost: {phrase}"


def test_tool_grind_budget_rule_lives_in_the_budgeted_action_module():
    """Rule 3a of the hand-written seed is action=budgeted, not action=persistent,
    so it is deliberately absent from the tool_grind compilation."""
    grind, _ = render_appendix(
        seed_spec("tool_grind", task_id="t", task_type="productivity_crawl")
    )
    budgeted, _ = render_appendix(
        seed_spec("evidence_search", task_id="t", task_type="search_budgeted")
    )
    phrase = "spend them like money"
    assert phrase in _rules(HAND_WRITTEN_TOOL_GRIND.read_text(encoding="utf-8"))
    assert phrase not in _rules(grind)
    assert phrase in _rules(budgeted)


# --- overlay integration --------------------------------------------------


def test_overlay_rejects_spec_declaring_absent_skills(tmp_path):
    dest = tmp_path / "overlay"
    dest.mkdir()
    (dest / "SYSTEM_APPENDIX.md").write_text("hi\n")
    spec = base_spec()
    spec["modules"]["capability"] = {"mode": "plus", "skills": ["agent-browser"]}
    (dest / SPEC_FILENAME).write_text(json.dumps(validate_spec(spec)))
    with pytest.raises(OverlayValidationError, match="agent-browser"):
        validate_overlay(dest)


def test_overlay_rejects_malformed_spec(tmp_path):
    dest = tmp_path / "overlay"
    dest.mkdir()
    (dest / SPEC_FILENAME).write_text("{not json")
    with pytest.raises(OverlayValidationError, match="invalid"):
        validate_overlay(dest)


def test_design_space_size_matches_the_spec_document():
    capability_modes = 3
    assert (
        len(MEMORY_VALUES) * len(PLANNING_VALUES) * len(ACTION_VALUES) * capability_modes
        == 192
    )
