import json

import pytest

from utils.pi_harness import (
    PI_THINKING_LEVELS,
    PiConfigurationError,
    PiHarnessAdapter,
    build_pi_command,
    build_pi_models_config,
    pi_runtime_env,
    validate_pi_options,
    write_pi_models_config,
)


def test_pi_thinking_levels_match_pinned_cli():
    assert PI_THINKING_LEVELS == (
        "off",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )


def test_validate_pi_options_reports_all_unsupported_options_in_sorted_order():
    with pytest.raises(PiConfigurationError) as raised:
        validate_pi_options(
            "openrouter/model",
            models_config={},
            lobster={"workspace": "/workspace", "name": "lobster", "env": "prod"},
            thinking="extreme",
            serper_api_key="accepted-but-not-configured",
        )

    assert raised.value.unsupported_options == [
        "--lobster-env",
        "--lobster-name",
        "--lobster-workspace",
        "--model",
        "--models-config",
        "--thinking",
    ]
    assert str(raised.value) == (
        "unsupported Pi options: --lobster-env, --lobster-name, "
        "--lobster-workspace, --model, --models-config, --thinking"
    )


@pytest.mark.parametrize("model", [None, "", "vllm/", "other/model"])
def test_validate_pi_options_rejects_invalid_model(model):
    with pytest.raises(PiConfigurationError) as raised:
        validate_pi_options(model)

    assert raised.value.unsupported_options == ["--model"]


@pytest.mark.parametrize("thinking", PI_THINKING_LEVELS)
def test_validate_pi_options_accepts_supported_thinking_and_serper(thinking):
    assert (
        validate_pi_options(
            "vllm/model-name", thinking=thinking, serper_api_key="serper-secret"
        )
        is None
    )


def test_validate_pi_options_ignores_empty_lobster_values():
    assert (
        validate_pi_options(
            "vllm/model-name",
            lobster={"workspace": None, "name": "", "env": None},
        )
        is None
    )


def test_build_pi_models_config_uses_model_suffix_and_text_input(capsys):
    config = build_pi_models_config(
        "vllm/org/model-name",
        "https://models.example.test",
        "top-secret-key",
    )

    assert config == {
        "providers": {
            "vllm": {
                "baseUrl": "https://models.example.test/v1",
                "apiKey": "top-secret-key",
                "api": "openai-completions",
                "models": [
                    {
                        "id": "org/model-name",
                        "name": "org/model-name",
                        "input": ["text"],
                    }
                ],
            }
        }
    }
    assert json.loads(json.dumps(config)) == config
    captured = capsys.readouterr()
    assert "top-secret-key" not in captured.out
    assert "top-secret-key" not in captured.err


def test_build_pi_models_config_declares_image_input_only_when_supported():
    config = build_pi_models_config(
        "vllm/model-name", "https://models.example.test/v1", "key", supports_images=True
    )

    assert config["providers"]["vllm"]["models"][0]["input"] == ["text", "image"]


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://models.example.test", "https://models.example.test/v1"),
        ("https://models.example.test/", "https://models.example.test/v1"),
        ("https://models.example.test/v1", "https://models.example.test/v1"),
        ("https://models.example.test/v1/", "https://models.example.test/v1"),
    ],
)
def test_build_pi_models_config_normalizes_v1_once(base_url, expected):
    config = build_pi_models_config("vllm/model-name", base_url, "key")

    assert config["providers"]["vllm"]["baseUrl"] == expected


def test_build_pi_command_uses_launcher_and_safe_argument_boundaries():
    prompt = 'Use "quoted" input\nthen write $HOME; $(touch /tmp/bad)'

    command = build_pi_command(
        "vllm/model-name",
        prompt,
        90,
        "/tmp/run/sessions",
        "/tmp/run/agent",
        launcher="/opt/pi-launcher",
    )

    assert command == [
        "/opt/pi-launcher",
        "--timeout-seconds",
        "90",
        "--agent-dir",
        "/tmp/run/agent",
        "--",
        "pi",
        "--print",
        "--mode",
        "json",
        "--model",
        "vllm/model-name",
        "--session-dir",
        "/tmp/run/sessions",
        prompt,
    ]
    assert command.count(prompt) == 1


def test_build_pi_command_adds_thinking_only_when_supplied():
    without_thinking = build_pi_command(
        "vllm/model-name", "prompt", 30, "/sessions", "/agent"
    )
    with_thinking = build_pi_command(
        "vllm/model-name",
        "prompt",
        30,
        "/sessions",
        "/agent",
        thinking="high",
    )

    assert "--thinking" not in without_thinking
    assert with_thinking[-3:] == ["--thinking", "high", "prompt"]


def test_pi_runtime_env_uses_run_local_directories():
    assert pi_runtime_env("/tmp/run/sessions", "/tmp/run/agent") == {
        "PI_CODING_AGENT_DIR": "/tmp/run/agent",
        "PI_CODING_AGENT_SESSION_DIR": "/tmp/run/sessions",
    }


def test_write_pi_models_config_atomically_writes_json_without_mutating_config(
    tmp_path,
):
    path = tmp_path / "agent" / "models.json"
    config = build_pi_models_config("vllm/model-name", "https://host", "key")
    original = json.loads(json.dumps(config))

    returned = write_pi_models_config(path, config)

    assert returned == path
    assert json.loads(path.read_text(encoding="utf-8")) == original
    assert config == original
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_pi_harness_adapter_builds_config_command_and_env():
    adapter = PiHarnessAdapter(launcher="/opt/pi-launcher")

    assert adapter.validate("vllm/model-name", thinking="low") is None
    assert (
        adapter.models_config("vllm/model-name", "https://host/v1", "key")["providers"][
            "vllm"
        ]["models"][0]["id"]
        == "model-name"
    )
    assert (
        adapter.command("vllm/model-name", "prompt", 10, "/sessions", "/agent")[0]
        == "/opt/pi-launcher"
    )
    assert adapter.runtime_env("/sessions", "/agent") == pi_runtime_env(
        "/sessions", "/agent"
    )
