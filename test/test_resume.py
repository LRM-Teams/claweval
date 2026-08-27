"""Unit tests for the find_completed_run resume logic."""
import json
import sys
import tempfile
import shutil
from pathlib import Path

# Patch OUTPUT_DIR before importing
import os
TEST_OUTPUT_DIR = None

# We need to test find_completed_run in isolation, so we'll import and patch.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "eval"))
sys.path.insert(0, os.path.dirname(__file__))


def setup_test_dir():
    """Create a temporary output directory for testing."""
    global TEST_OUTPUT_DIR
    TEST_OUTPUT_DIR = Path(tempfile.mkdtemp(prefix="wcb_test_resume_"))
    return TEST_OUTPUT_DIR


def teardown_test_dir():
    """Remove the temporary output directory."""
    if TEST_OUTPUT_DIR and TEST_OUTPUT_DIR.exists():
        shutil.rmtree(TEST_OUTPUT_DIR)


def make_run_dir(output_dir, category, task_id_ori, dirname):
    """Helper: create a run subdirectory."""
    d = output_dir / category / task_id_ori / dirname
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_no_output_dir():
    """When output dir for the task doesn't exist, returns None."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        result = run_batch.find_completed_run(
            "06_Safety_Alignment",
            "06_Safety_Alignment_task_1_file_overwrite",
            "glm-5.1",
            ""
        )
        assert result is None, f"Expected None, got {result}"
        print("PASS: test_no_output_dir")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_no_score_json():
    """When run dir exists but no score.json, returns None."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        d = make_run_dir(output_dir, "06_Safety_Alignment",
                         "06_Safety_Alignment_task_1_file_overwrite",
                         "glm-5.1_20260529_1026_475795")
        (d / "agent.log").write_text("")
        (d / "gateway.log").write_text("some log")

        result = run_batch.find_completed_run(
            "06_Safety_Alignment",
            "06_Safety_Alignment_task_1_file_overwrite",
            "glm-5.1",
            ""
        )
        assert result is None, f"Expected None, got {result}"
        print("PASS: test_no_score_json")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_score_json_with_error():
    """When score.json contains an error key, returns None."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        d = make_run_dir(output_dir, "06_Safety_Alignment",
                         "06_Safety_Alignment_task_1_file_overwrite",
                         "glm-5.1_20260601_0239_490991")
        (d / "score.json").write_text(json.dumps({"error": "grade script failed"}))

        result = run_batch.find_completed_run(
            "06_Safety_Alignment",
            "06_Safety_Alignment_task_1_file_overwrite",
            "glm-5.1",
            ""
        )
        assert result is None, f"Expected None, got {result}"
        print("PASS: test_score_json_with_error")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_score_json_missing_overall_score():
    """When score.json has no overall_score, returns None."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        d = make_run_dir(output_dir, "06_Safety_Alignment",
                         "06_Safety_Alignment_task_1_file_overwrite",
                         "glm-5.1_20260601_0239_490991")
        (d / "score.json").write_text(json.dumps({"some_metric": 0.5}))

        result = run_batch.find_completed_run(
            "06_Safety_Alignment",
            "06_Safety_Alignment_task_1_file_overwrite",
            "glm-5.1",
            ""
        )
        assert result is None, f"Expected None, got {result}"
        print("PASS: test_score_json_missing_overall_score")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_valid_score_json():
    """When score.json is valid, returns a result dict with scores."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        d = make_run_dir(output_dir, "06_Safety_Alignment",
                         "06_Safety_Alignment_task_1_file_overwrite",
                         "glm-5.1_20260601_0239_490991")
        scores = {"mae_pdf_valid": 1.0, "overall_score": 0.5}
        (d / "score.json").write_text(json.dumps(scores))

        result = run_batch.find_completed_run(
            "06_Safety_Alignment",
            "06_Safety_Alignment_task_1_file_overwrite",
            "glm-5.1",
            ""
        )
        assert result is not None, "Expected a result dict"
        assert result["scores"] == scores
        assert result["error"] is None
        assert "task_id" in result
        print("PASS: test_valid_score_json")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_multiple_dirs_picks_newest():
    """When multiple valid run dirs exist, picks the newest (by dir name sort)."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        category = "06_Safety_Alignment"
        task_id = "06_Safety_Alignment_task_1_file_overwrite"

        # Older run
        d1 = make_run_dir(output_dir, category, task_id, "glm-5.1_20260529_1026_aaaaaa")
        (d1 / "score.json").write_text(json.dumps({"overall_score": 0.3}))

        # Newer run
        d2 = make_run_dir(output_dir, category, task_id, "glm-5.1_20260601_0239_bbbbbb")
        (d2 / "score.json").write_text(json.dumps({"overall_score": 0.7}))

        result = run_batch.find_completed_run(category, task_id, "glm-5.1", "")
        assert result is not None
        assert result["scores"]["overall_score"] == 0.7, f"Expected 0.7, got {result['scores']['overall_score']}"
        print("PASS: test_multiple_dirs_picks_newest")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_different_model_not_matched():
    """Run dirs for a different model are not matched."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        d = make_run_dir(output_dir, "06_Safety_Alignment",
                         "06_Safety_Alignment_task_1_file_overwrite",
                         "gpt-5.4_20260601_0239_490991")
        (d / "score.json").write_text(json.dumps({"overall_score": 0.9}))

        result = run_batch.find_completed_run(
            "06_Safety_Alignment",
            "06_Safety_Alignment_task_1_file_overwrite",
            "glm-5.1",  # looking for glm-5.1, not gpt-5.4
            ""
        )
        assert result is None, f"Expected None, got {result}"
        print("PASS: test_different_model_not_matched")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_lobster_prefix():
    """When lobster_prefix is set, only matches dirs with that prefix."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        category = "06_Safety_Alignment"
        task_id = "06_Safety_Alignment_task_1_file_overwrite"

        # Run without lobster prefix
        d1 = make_run_dir(output_dir, category, task_id, "glm-5.1_20260601_0239_aaaaaa")
        (d1 / "score.json").write_text(json.dumps({"overall_score": 0.5}))

        # Run with lobster prefix
        d2 = make_run_dir(output_dir, category, task_id, "mylobster_glm-5.1_20260601_0300_bbbbbb")
        (d2 / "score.json").write_text(json.dumps({"overall_score": 0.8}))

        # Search with lobster prefix should find d2
        result = run_batch.find_completed_run(category, task_id, "glm-5.1", "mylobster_")
        assert result is not None
        assert result["scores"]["overall_score"] == 0.8

        # Search without lobster prefix should find d1
        result2 = run_batch.find_completed_run(category, task_id, "glm-5.1", "")
        assert result2 is not None
        assert result2["scores"]["overall_score"] == 0.5

        print("PASS: test_lobster_prefix")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


def test_usage_json_loaded():
    """When usage.json exists, it's loaded into the result."""
    output_dir = setup_test_dir()
    try:
        import run_batch
        orig = run_batch.OUTPUT_DIR
        run_batch.OUTPUT_DIR = output_dir

        d = make_run_dir(output_dir, "06_Safety_Alignment",
                         "06_Safety_Alignment_task_1_file_overwrite",
                         "glm-5.1_20260601_0239_490991")
        (d / "score.json").write_text(json.dumps({"overall_score": 0.5}))
        usage = {"input_tokens": 1000, "output_tokens": 500, "cost_usd": 0.05,
                 "elapsed_time": 120.5, "cache_read_tokens": 0,
                 "cache_write_tokens": 0, "total_tokens": 1500, "request_count": 3}
        (d / "usage.json").write_text(json.dumps(usage))

        result = run_batch.find_completed_run(
            "06_Safety_Alignment",
            "06_Safety_Alignment_task_1_file_overwrite",
            "glm-5.1",
            ""
        )
        assert result is not None
        assert result["usage"]["input_tokens"] == 1000
        assert result["usage"]["cost_usd"] == 0.05
        print("PASS: test_usage_json_loaded")
    finally:
        run_batch.OUTPUT_DIR = orig
        teardown_test_dir()


if __name__ == "__main__":
    test_no_output_dir()
    test_no_score_json()
    test_score_json_with_error()
    test_score_json_missing_overall_score()
    test_valid_score_json()
    test_multiple_dirs_picks_newest()
    test_different_model_not_matched()
    test_lobster_prefix()
    test_usage_json_loaded()
    print("\n=== All 9 tests passed ===")
