import subprocess

import pytest

from utils.harness import (
    HarnessDetectionError,
    HarnessKind,
    apply_price_override,
    detect_harness,
    empty_usage,
)


class Runner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self.results.pop(0)


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.mark.parametrize(
    ("probe_output", "expected"),
    [
        ("openclaw=0\npi=127\n", HarnessKind.OPENCLAW),
        ("openclaw=127\npi=0\n", HarnessKind.PI),
    ],
)
def test_detect_harness_uses_one_shell_free_probe(probe_output, expected):
    runner = Runner(completed(stdout=probe_output))

    assert detect_harness("task-container", runner=runner) is expected

    assert len(runner.calls) == 1
    command, kwargs = runner.calls[0]
    assert command[:3] == ["docker", "exec", "task-container"]
    assert "command -v openclaw" in command[-1]
    assert "command -v pi" in command[-1]
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs.get("shell", False) is False


def test_detect_harness_reports_nonzero_probe_failure_without_retry():
    runner = Runner(
        completed(returncode=125),
        completed(stdout="openclaw=127\npi=0\n"),
    )

    with pytest.raises(HarnessDetectionError, match="probe failed with exit code 125"):
        detect_harness("task-container", runner=runner)

    assert len(runner.calls) == 1


def test_detect_harness_reports_probe_failure_after_retry():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        raise OSError("docker unavailable")

    with pytest.raises(HarnessDetectionError, match="probe failed after 2 attempts"):
        detect_harness("task-container", runner=runner)

    assert len(calls) == 2


def test_detect_harness_retries_probe_exceptions_once():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls) == 1:
            raise OSError("docker unavailable")
        return completed(stdout="openclaw=0\npi=127\n")

    assert detect_harness("task-container", runner=runner) is HarnessKind.OPENCLAW
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("probe_output", "message"),
    [
        ("openclaw=0\npi=0\n", "multiple harnesses detected"),
        ("openclaw=127\npi=127\n", "no supported harness detected"),
    ],
)
def test_detect_harness_rejects_both_and_neither_without_retry(probe_output, message):
    runner = Runner(completed(stdout=probe_output))

    with pytest.raises(HarnessDetectionError, match=message):
        detect_harness("task-container", runner=runner)

    assert len(runner.calls) == 1


def test_empty_usage_has_existing_keys_and_elapsed_time():
    assert empty_usage(1.25) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "request_count": 0,
        "elapsed_time": 1.25,
    }


def test_price_override_requires_both_prices_and_defaults_cache_read_to_zero():
    usage = empty_usage()
    usage.update(
        input_tokens=1_000_000,
        output_tokens=500_000,
        cache_read_tokens=250_000,
        cost_usd=99.0,
    )

    assert (
        apply_price_override(usage.copy(), input_price=2.0, output_price=None)[
            "cost_usd"
        ]
        == 99.0
    )
    assert (
        apply_price_override(usage.copy(), input_price=None, output_price=4.0)[
            "cost_usd"
        ]
        == 99.0
    )
    assert (
        apply_price_override(usage.copy(), input_price=2.0, output_price=4.0)[
            "cost_usd"
        ]
        == 4.0
    )
    assert (
        apply_price_override(
            usage.copy(), input_price=2.0, output_price=4.0, cache_read_price=1.0
        )["cost_usd"]
        == 4.25
    )
