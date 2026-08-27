import json

import pytest

from utils.pi_session import (
    PiSessionSelectionError,
    copy_session_bytes,
    extract_usage_from_jsonl,
    select_session_candidate,
    validate_session_candidate,
)


def record(data):
    return json.dumps(data, separators=(",", ":")).encode() + b"\n"


def write_session(path, *entries, header=None, prefix=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = header or {"type": "session", "cwd": "/tmp_workspace"}
    path.write_bytes(
        prefix + record(header) + b"".join(record(entry) for entry in entries)
    )
    return path


def user_entry(marker="run-123", entry_id="user-1"):
    return {
        "type": "message",
        "id": entry_id,
        "message": {"role": "user", "content": marker},
    }


def test_validate_candidate_uses_first_valid_record_and_user_marker(tmp_path):
    candidate = write_session(
        tmp_path / "session.jsonl", user_entry(), prefix=b"truncated {\n"
    )

    assert validate_session_candidate(
        candidate, expected_cwd="/tmp_workspace", run_marker="run-123"
    )

    candidate.write_bytes(
        record({"type": "message"}) + record({"type": "session"}) + record(user_entry())
    )
    assert not validate_session_candidate(
        candidate, expected_cwd="/tmp_workspace", run_marker="run-123"
    )


def test_validate_candidate_requires_expected_cwd_and_marker_only_in_user_messages(
    tmp_path,
):
    no_cwd = write_session(
        tmp_path / "no-cwd.jsonl", user_entry(), header={"type": "session"}
    )
    wrong_cwd = write_session(
        tmp_path / "wrong-cwd.jsonl",
        user_entry(),
        header={"type": "session", "cwd": "/root"},
    )
    assistant_marker = write_session(
        tmp_path / "assistant.jsonl",
        {
            "type": "message",
            "id": "a",
            "message": {"role": "assistant", "content": "run-123"},
        },
    )

    assert not validate_session_candidate(
        no_cwd, expected_cwd="/tmp_workspace", run_marker="run-123"
    )
    assert not validate_session_candidate(
        wrong_cwd, expected_cwd="/tmp_workspace", run_marker="run-123"
    )
    assert not validate_session_candidate(
        assistant_marker, expected_cwd="/tmp_workspace", run_marker="run-123"
    )


def test_validate_candidate_allows_header_only_when_allow_empty(tmp_path):
    candidate = write_session(tmp_path / "empty.jsonl")

    assert not validate_session_candidate(
        candidate, expected_cwd="/tmp_workspace", run_marker="run-123"
    )
    assert validate_session_candidate(
        candidate, expected_cwd="/tmp_workspace", run_marker="run-123", allow_empty=True
    )


def test_select_session_candidate_recurses_and_selects_exactly_one(tmp_path):
    root = tmp_path / "sessions"
    selected = write_session(root / "nested" / "selected.jsonl", user_entry())
    write_session(root / "wrong.jsonl", user_entry("another-run"))
    (root / "notes.txt").write_text("ignored", encoding="utf-8")

    assert (
        select_session_candidate(
            root, expected_cwd="/tmp_workspace", run_marker="run-123"
        )
        == selected
    )


def test_select_session_candidate_has_deterministic_missing_and_ambiguous_errors(
    tmp_path,
):
    root = tmp_path / "sessions"
    root.mkdir()

    with pytest.raises(PiSessionSelectionError) as missing:
        select_session_candidate(
            root, expected_cwd="/tmp_workspace", run_marker="run-123"
        )
    assert str(missing.value) == "no valid Pi session candidate"

    write_session(root / "z.jsonl", user_entry(entry_id="z"))
    write_session(root / "a.jsonl", user_entry(entry_id="a"))
    with pytest.raises(PiSessionSelectionError) as ambiguous:
        select_session_candidate(
            root, expected_cwd="/tmp_workspace", run_marker="run-123"
        )
    assert str(ambiguous.value) == "ambiguous Pi session candidates: a.jsonl, z.jsonl"


def test_select_session_candidate_rejects_symlinks_and_escaping_paths(tmp_path):
    root = tmp_path / "sessions"
    root.mkdir()
    outside = write_session(tmp_path / "outside.jsonl", user_entry())
    symlink = root / "linked.jsonl"
    symlink.symlink_to(outside)

    with pytest.raises(
        PiSessionSelectionError, match="unsafe Pi session candidate: linked.jsonl"
    ):
        select_session_candidate(
            root, expected_cwd="/tmp_workspace", run_marker="run-123"
        )

    symlink.unlink()
    with pytest.raises(PiSessionSelectionError, match="escapes session root"):
        select_session_candidate(
            root,
            expected_cwd="/tmp_workspace",
            run_marker="run-123",
            candidates=[outside],
        )


def test_select_session_candidate_rejects_symlinked_directories(tmp_path):
    root = tmp_path / "sessions"
    real_directory = root / "real"
    candidate = write_session(real_directory / "session.jsonl", user_entry())
    linked_directory = root / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(PiSessionSelectionError, match="unsafe Pi session candidate"):
        select_session_candidate(
            root,
            expected_cwd="/tmp_workspace",
            run_marker="run-123",
            candidates=[linked_directory / candidate.name],
        )


def test_select_session_candidate_allows_symlink_at_root_boundary(tmp_path):
    real_root = tmp_path / "sessions"
    candidate = write_session(real_root / "session.jsonl", user_entry())
    linked_root = tmp_path / "linked-sessions"
    linked_root.symlink_to(real_root, target_is_directory=True)

    assert (
        select_session_candidate(
            linked_root,
            expected_cwd="/tmp_workspace",
            run_marker="run-123",
        )
        == linked_root / candidate.name
    )


def test_copy_session_bytes_preserves_original_bytes(tmp_path):
    source = tmp_path / "source.jsonl"
    destination = tmp_path / "output" / "chat.jsonl"
    original = b'{"type":"session"}\r\n{malformed\xff\n'
    source.write_bytes(original)

    copy_session_bytes(source, destination)

    assert destination.read_bytes() == original


def test_extract_usage_counts_known_records_on_all_branches_only(tmp_path):
    path = tmp_path / "session.jsonl"
    retained = {
        "type": "message",
        "id": "old",
        "message": {"role": "assistant", "usage": {"input": 999, "totalTokens": 999}},
    }
    entries = [
        user_entry(),
        {
            "type": "message",
            "id": "a",
            "parentId": "branch-1",
            "message": {
                "role": "assistant",
                "usage": {
                    "input": 10,
                    "output": 2,
                    "cacheRead": 3,
                    "cacheWrite": 1,
                    "totalTokens": 20,
                    "cost": {"total": 0.1},
                },
            },
        },
        {
            "type": "message",
            "id": "b",
            "parentId": "abandoned-branch",
            "message": {
                "role": "assistant",
                "usage": {"input": 4, "output": 1, "cost": {"total": 0.02}},
            },
        },
        {
            "type": "compaction",
            "id": "c",
            "usage": {
                "input": 2,
                "output": 3,
                "totalTokens": 5,
                "cost": {"total": 0.03},
            },
            "retainedTail": [retained],
        },
        {
            "type": "branch_summary",
            "id": "d",
            "usage": {
                "input": 1,
                "output": 1,
                "cacheRead": 1,
                "cacheWrite": 1,
                "totalTokens": 4,
                "cost": {"total": 0.04},
            },
        },
        {"type": "message_update", "id": "stream", "usage": {"input": 500}},
    ]
    write_session(path, *entries)

    usage = extract_usage_from_jsonl(path)

    assert usage == {
        "input_tokens": 17,
        "output_tokens": 7,
        "cache_read_tokens": 4,
        "cache_write_tokens": 2,
        "total_tokens": 34,
        "cost_usd": 0.19,
        "request_count": 4,
        "elapsed_time": 0.0,
        "malformed_line_count": 0,
        "missing_entry_id_count": 0,
        "duplicate_entry_count": 0,
        "conflicting_duplicate_count": 0,
        "unknown_usage_entry_count": 1,
        "total_token_mismatch_count": 1,
    }


def test_extract_usage_handles_malformed_duplicates_and_missing_ids(tmp_path):
    path = tmp_path / "session.jsonl"
    first = {
        "type": "message",
        "id": "dup",
        "message": {
            "role": "assistant",
            "usage": {"input": 1, "totalTokens": "invalid"},
        },
    }
    conflicting = {
        "type": "message",
        "id": "dup",
        "message": {"role": "assistant", "usage": {"input": 99}},
    }
    missing_id = {
        "type": "compaction",
        "usage": {"input": 50, "cost": {"total": 10}},
    }
    path.write_bytes(
        record({"type": "session"})
        + b"not json\n"
        + record(first)
        + record(first)
        + record(conflicting)
        + record(missing_id)
        + b'{"type":"message"'
    )

    usage = extract_usage_from_jsonl(path)

    assert usage["input_tokens"] == 1
    assert usage["total_tokens"] == 1
    assert usage["cost_usd"] == 0.0
    assert usage["request_count"] == 1
    assert usage["malformed_line_count"] == 2
    assert usage["missing_entry_id_count"] == 1
    assert usage["duplicate_entry_count"] == 1
    assert usage["conflicting_duplicate_count"] == 1


def test_extract_usage_compares_duplicate_record_bytes_and_ignores_json_scalars(
    tmp_path,
):
    path = tmp_path / "session.jsonl"
    first = b'{"type":"compaction","id":"same","usage":{"input":1}}\n'
    reordered = b'{"id":"same","type":"compaction","usage":{"input":1}}\n'
    path.write_bytes(record({"type": "session"}) + b"42\n" + first + first + reordered)

    usage = extract_usage_from_jsonl(path)

    assert usage["input_tokens"] == 1
    assert usage["duplicate_entry_count"] == 1
    assert usage["conflicting_duplicate_count"] == 1


def test_extract_usage_returns_zero_diagnostics_for_missing_file(tmp_path):
    usage = extract_usage_from_jsonl(tmp_path / "missing.jsonl")

    assert usage["request_count"] == 0
    assert usage["malformed_line_count"] == 0
    assert set(usage) == {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
        "cost_usd",
        "request_count",
        "elapsed_time",
        "malformed_line_count",
        "missing_entry_id_count",
        "duplicate_entry_count",
        "conflicting_duplicate_count",
        "unknown_usage_entry_count",
        "total_token_mismatch_count",
    }
