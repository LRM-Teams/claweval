import json
import math
import shutil
from pathlib import Path

from .harness import empty_usage


class PiSessionSelectionError(RuntimeError):
    pass


def _first_record(path):
    try:
        with Path(path).open("rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    return json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
    except OSError:
        return None
    return None


def validate_session_candidate(
    path, expected_cwd=None, run_marker=None, allow_empty=False
):
    header = _first_record(path)
    if not isinstance(header, dict) or header.get("type") != "session":
        return False
    if expected_cwd is not None and header.get("cwd") != expected_cwd:
        return False
    if allow_empty:
        return True
    if not run_marker:
        return False
    try:
        with Path(path).open("rb") as handle:
            next_valid = False
            for line in handle:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not next_valid:
                    next_valid = True
                    continue
                if (
                    isinstance(entry, dict)
                    and entry.get("type") == "message"
                    and isinstance(entry.get("message"), dict)
                    and entry["message"].get("role") == "user"
                    and run_marker in _content_text(entry["message"].get("content"))
                ):
                    return True
    except OSError:
        return False
    return False


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(_content_text(item) for item in content)
    if isinstance(content, dict):
        return " ".join(_content_text(value) for value in content.values())
    return ""


def _safe_candidate(path, root):
    path = Path(path)
    root_path = Path(root).absolute()
    root_resolved = root_path.resolve()
    path_absolute = path.absolute()
    for candidate_root in (root_path, root_resolved):
        try:
            relative = path_absolute.relative_to(candidate_root)
            current = candidate_root
            break
        except ValueError:
            continue
    else:
        raise PiSessionSelectionError("Pi session candidate escapes session root")
    for component in relative.parts:
        current /= component
        if current.is_symlink():
            raise PiSessionSelectionError(f"unsafe Pi session candidate: {path.name}")
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PiSessionSelectionError(
            "Pi session candidate escapes session root"
        ) from exc
    if not resolved.is_file():
        return False
    return True


def select_session_candidate(
    root, expected_cwd=None, run_marker=None, allow_empty=False, candidates=None
):
    root = Path(root)
    if candidates is None:
        found = sorted(
            root.rglob("*.jsonl"), key=lambda path: path.relative_to(root).as_posix()
        )
    else:
        found = list(candidates)
    valid = []
    for candidate in found:
        if not _safe_candidate(candidate, root):
            continue
        if validate_session_candidate(candidate, expected_cwd, run_marker, allow_empty):
            valid.append(Path(candidate))
    if not valid:
        raise PiSessionSelectionError("no valid Pi session candidate")
    if len(valid) != 1:
        names = ", ".join(sorted(path.relative_to(root).as_posix() for path in valid))
        raise PiSessionSelectionError(f"ambiguous Pi session candidates: {names}")
    return valid[0]


def copy_session_bytes(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (
        Path(source).open("rb") as source_handle,
        destination.open("wb") as destination_handle,
    ):
        shutil.copyfileobj(source_handle, destination_handle)


def _number(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else 0
    )


def _usage_values(usage):
    if not isinstance(usage, dict):
        return None
    values = {
        "input": _number(usage.get("input")),
        "output": _number(usage.get("output")),
        "cacheRead": _number(usage.get("cacheRead")),
        "cacheWrite": _number(usage.get("cacheWrite")),
    }
    components = sum(values.values())
    total = usage.get("totalTokens")
    if (
        isinstance(total, (int, float))
        and not isinstance(total, bool)
        and math.isfinite(total)
        and total >= 0
    ):
        total_value = total
        mismatch = total != components
    else:
        total_value = components
        mismatch = False
    cost = usage.get("cost")
    cost_total = _number(cost.get("total")) if isinstance(cost, dict) else 0.0
    values.update(total=total_value, cost=cost_total, mismatch=mismatch)
    return values


def extract_usage_from_jsonl(jsonl_path):
    totals = empty_usage()
    totals.update(
        malformed_line_count=0,
        missing_entry_id_count=0,
        duplicate_entry_count=0,
        conflicting_duplicate_count=0,
        unknown_usage_entry_count=0,
        total_token_mismatch_count=0,
    )
    path = Path(jsonl_path)
    if not path.exists():
        return totals
    seen = {}
    with path.open("rb") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                entry = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                totals["malformed_line_count"] += 1
                continue
            usage = None
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "message" and isinstance(
                entry.get("message"), dict
            ):
                if (
                    entry["message"].get("role") == "assistant"
                    and "usage" in entry["message"]
                ):
                    usage = entry["message"]["usage"]
            elif (
                entry.get("type") in ("compaction", "branch_summary")
                and "usage" in entry
            ):
                usage = entry["usage"]
            if usage is None:
                if "usage" in entry:
                    totals["unknown_usage_entry_count"] += 1
                continue
            entry_id = entry.get("id")
            if not isinstance(entry_id, str) or not entry_id:
                totals["missing_entry_id_count"] += 1
                continue
            raw_record = raw_line.rstrip(b"\r\n")
            if entry_id in seen:
                if seen[entry_id] == raw_record:
                    totals["duplicate_entry_count"] += 1
                else:
                    totals["conflicting_duplicate_count"] += 1
                continue
            seen[entry_id] = raw_record
            values = _usage_values(usage)
            if values is None:
                continue
            totals["input_tokens"] += values["input"]
            totals["output_tokens"] += values["output"]
            totals["cache_read_tokens"] += values["cacheRead"]
            totals["cache_write_tokens"] += values["cacheWrite"]
            totals["total_tokens"] += values["total"]
            totals["cost_usd"] += values["cost"]
            totals["request_count"] += 1
            if values["mismatch"]:
                totals["total_token_mismatch_count"] += 1
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return totals
