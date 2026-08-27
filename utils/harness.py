import subprocess
from enum import Enum


class HarnessKind(str, Enum):
    OPENCLAW = "openclaw"
    PI = "pi"


class HarnessDetectionError(RuntimeError):
    pass


def _probe_command(container_name):
    script = (
        "command -v openclaw >/dev/null 2>&1; "
        "printf 'openclaw=%s\\n' \"$?\"; "
        "command -v pi >/dev/null 2>&1; "
        "printf 'pi=%s\\n' \"$?\""
    )
    return ["docker", "exec", container_name, "sh", "-c", script]


def detect_harness(container_name, runner=subprocess.run):
    command = _probe_command(container_name)
    for _ in range(2):
        try:
            result = runner(command, capture_output=True, text=True)
        except OSError:
            continue
        if result.returncode != 0:
            detail = (result.stderr or "").strip()
            message = f"probe failed with exit code {result.returncode}"
            if detail:
                message = f"{message}: {detail}"
            raise HarnessDetectionError(message)
        break
    else:
        raise HarnessDetectionError("probe failed after 2 attempts")

    values = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator and name in ("openclaw", "pi"):
            values[name] = value == "0"
    available = [name for name in ("openclaw", "pi") if values.get(name)]
    if len(available) == 2:
        raise HarnessDetectionError("multiple harnesses detected")
    if not available:
        raise HarnessDetectionError("no supported harness detected")
    return HarnessKind(available[0])


def empty_usage(elapsed_time=0.0):
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "request_count": 0,
        "elapsed_time": elapsed_time,
    }


def apply_price_override(
    usage, input_price=None, output_price=None, cache_read_price=None
):
    if input_price is not None and output_price is not None:
        cache_price = cache_read_price if cache_read_price is not None else 0.0
        usage["cost_usd"] = round(
            (
                usage.get("input_tokens", 0) * input_price
                + usage.get("cache_read_tokens", 0) * cache_price
                + usage.get("output_tokens", 0) * output_price
            )
            / 1_000_000,
            6,
        )
    return usage
