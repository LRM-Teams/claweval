"""P4.2 acceptance: capability registry, task-level overlay build, safety gate.

Offline only: no container, no API call.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from utils.harness_compile import CompileError, build_task_overlay  # noqa: E402
from utils.harness_overlay import validate_overlay  # noqa: E402
from utils.harness_policy import (  # noqa: E402
    DEFAULT_TASK_BLACKLIST,
    assert_task_allowed,
    load_task_blacklist,
)
from utils.harness_registry import (  # noqa: E402
    available_skills,
    build_registry,
    discover_skill_bundles,
    skill_sources,
)
from utils.harness_spec import (  # noqa: E402
    SpecValidationError,
    default_task_type,
    validate_spec,
)


@pytest.fixture
def fake_skills(tmp_path):
    root = tmp_path / "skills"
    for name, description in (
        ("agent-browser", "Headless browser automation CLI"),
        ("video-frames", "Extract frames from video files"),
    ):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"
        )
    # Task-local bundle: injected by its own task, not a general building block.
    local = root / "03_task1"
    local.mkdir(parents=True)
    (local / "SKILL.md").write_text("# local\n\nSome prose paragraph here.\n")
    return root


def make_task(**overrides):
    task = {
        "task_id": "04_Search_Retrieval_task_1_google_scholar_search",
        "category": "04_Search_Retrieval",
        "skills": "agent-browser",
        "env": "JUDGE_MODEL_KEY\nJUDGE_MODEL_URL",
        "timeout_seconds": 300,
    }
    task.update(overrides)
    return task


# --- capability registry C_tau --------------------------------------------


def test_discovery_hides_task_local_bundles(fake_skills):
    assert sorted(discover_skill_bundles(fake_skills)) == [
        "agent-browser",
        "video-frames",
    ]
    assert "03_task1" in discover_skill_bundles(fake_skills, include_task_local=True)


def test_summary_prefers_frontmatter_description(fake_skills):
    bundles = discover_skill_bundles(fake_skills)
    assert bundles["agent-browser"]["summary"] == "Headless browser automation CLI"


def test_summary_falls_back_to_first_paragraph(fake_skills):
    bundles = discover_skill_bundles(fake_skills, include_task_local=True)
    assert bundles["03_task1"]["summary"] == "Some prose paragraph here."


def test_mountable_excludes_skills_the_task_already_has(fake_skills):
    registry = build_registry(make_task(), skills_root=fake_skills, env={})
    assert registry["task_skills"] == ["agent-browser"]
    assert available_skills(registry) == ["video-frames"]
    assert registry["skills"]["agent-browser"]["already_mounted"] is True


def test_env_presence_is_reported_without_leaking_values(fake_skills):
    registry = build_registry(
        make_task(),
        skills_root=fake_skills,
        env={"JUDGE_MODEL_KEY": "sk-secret", "JUDGE_MODEL_URL": "   "},
    )
    assert registry["env_present"] == ["JUDGE_MODEL_KEY"]
    assert registry["env_missing"] == ["JUDGE_MODEL_URL"]
    assert "sk-secret" not in str(registry)


def test_empty_sections_yield_empty_lists(fake_skills):
    registry = build_registry(
        make_task(skills="", env=None), skills_root=fake_skills, env={}
    )
    assert registry["task_skills"] == []
    assert registry["env_keys"] == []


def test_skill_sources_rejects_unknown_name(fake_skills):
    registry = build_registry(make_task(), skills_root=fake_skills, env={})
    with pytest.raises(KeyError, match="ghost"):
        skill_sources(["ghost"], registry, skills_root=fake_skills)


def test_real_repo_registry_offers_the_general_bundles():
    registry = build_registry(make_task(), env={})
    assert "video-frames" in registry["mountable"]
    assert not any(name.startswith("03_task") for name in registry["skills"])


# --- task_type defaults ---------------------------------------------------


@pytest.mark.parametrize(
    "category,expected",
    [
        ("01_Productivity_Flow", "productivity_crawl"),
        ("02_Code_Intelligence", "code_repo"),
        ("03_Social_Interaction", "chat_extract"),
        ("04_Search_Retrieval", "search_deep"),
        ("05_Creative_Synthesis", "creative_media"),
    ],
)
def test_category_maps_to_a_task_type(category, expected):
    assert default_task_type(category) == expected


def test_safety_category_has_no_default_task_type():
    """None of the declared task types describes 06_Safety_Alignment, so it must
    be named explicitly rather than silently mislabelled."""
    with pytest.raises(SpecValidationError, match="no default task_type"):
        default_task_type("06_Safety_Alignment")


# --- build_task_overlay ---------------------------------------------------


def test_seed_builds_a_valid_overlay(tmp_path):
    dest = tmp_path / "overlay"
    audit = build_task_overlay(make_task(), dest, seed="evidence_search")
    assert audit["seed_name"] == "evidence_search"
    assert audit["task_type"] == "search_deep"
    assert audit["registry"]["task_skills"] == ["agent-browser"]
    assert validate_overlay(dest) == dest


def test_explicit_task_type_overrides_the_category_default(tmp_path):
    audit = build_task_overlay(
        make_task(), tmp_path / "o", seed="react", task_type="search_budgeted"
    )
    assert audit["task_type"] == "search_budgeted"


def test_needs_exactly_one_of_seed_or_spec(tmp_path):
    with pytest.raises(CompileError, match="exactly one"):
        build_task_overlay(make_task(), tmp_path / "a")
    with pytest.raises(CompileError, match="exactly one"):
        build_task_overlay(
            make_task(), tmp_path / "b", seed="react", spec={"spec_version": 1}
        )


def test_spec_requesting_an_unmountable_skill_fails_offline(tmp_path):
    """agent-browser is already the task's own skill, so an overlay re-mounting
    it would only shadow it with an identical copy."""
    spec = validate_spec(
        {
            "spec_version": 1,
            "task_id": make_task()["task_id"],
            "task_type": "search_deep",
            "modules": {
                "memory": "notes",
                "planning": "checklist",
                "action": "persistent",
                "capability": {"mode": "plus", "skills": ["agent-browser"]},
            },
            "settings": {},
            "rationale": "",
            "reference_ids": [],
        }
    )
    with pytest.raises(SpecValidationError, match="agent-browser"):
        build_task_overlay(make_task(), tmp_path / "o", spec=spec)


def test_spec_mounting_a_general_bundle_succeeds(tmp_path):
    spec = {
        "spec_version": 1,
        "task_id": make_task()["task_id"],
        "task_type": "search_deep",
        "modules": {
            "memory": "notes",
            "planning": "checklist",
            "action": "persistent",
            "capability": {"mode": "plus", "skills": ["video-frames"]},
        },
        "settings": {},
        "rationale": "",
        "reference_ids": [],
    }
    dest = tmp_path / "overlay"
    audit = build_task_overlay(make_task(), dest, spec=spec)
    assert audit["skills"] == ["video-frames"]
    assert (dest / "skills" / "video-frames").is_dir()
    validate_overlay(dest)


def test_unknown_seed_is_rejected(tmp_path):
    with pytest.raises(CompileError, match="unknown seed"):
        build_task_overlay(make_task(), tmp_path / "o", seed="nope")


def test_safety_task_without_explicit_type_is_refused(tmp_path):
    task = make_task(
        task_id="06_Safety_Alignment_task_9_misinformation",
        category="06_Safety_Alignment",
    )
    with pytest.raises(SpecValidationError):
        build_task_overlay(task, tmp_path / "o", seed="react")


# --- safety policy --------------------------------------------------------


@pytest.mark.parametrize("task_id", DEFAULT_TASK_BLACKLIST)
def test_injection_tasks_cannot_have_a_harness_fabricated(task_id):
    with pytest.raises(ValueError, match="blacklisted"):
        assert_task_allowed(task_id, flag="--jit-harness")


def test_ordinary_task_is_allowed():
    assert_task_allowed("04_Search_Retrieval_task_1_google_scholar_search",
                        flag="--jit-harness")


def test_repo_config_still_blacklists_the_injection_tasks():
    """run_batch and evolve_task now share this list; if the config drifts, the
    JIT entry point silently opens up."""
    configured = set(load_task_blacklist())
    assert set(DEFAULT_TASK_BLACKLIST) <= configured


def test_blacklist_falls_back_when_config_is_absent(tmp_path):
    assert load_task_blacklist(tmp_path / "missing.yaml") == list(
        DEFAULT_TASK_BLACKLIST
    )
