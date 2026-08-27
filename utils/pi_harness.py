from pathlib import Path

from .run_artifacts import atomic_write_json


PI_THINKING_LEVELS = (
    "off",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

# Providers the Pi harness can inject into the container models.json.
# Env var names follow the same pattern as the host .env file.
PI_PROVIDER_ENV = {
    "vllm": ("ZHIZENGZENG_API_URL", "ZHIZENGZENG_API_KEY"),
    "deepseek": ("DEEPSEEK_API_URL", "DEEPSEEK_API_KEY"),
}

# Extra per-model fields, mirroring the global ~/.pi/agent/models.json setup.
PI_PROVIDER_MODEL_EXTRAS = {
    "deepseek": {"contextWindow": 128000, "maxTokens": 8192},
}


class PiConfigurationError(ValueError):
    def __init__(self, unsupported_options):
        self.unsupported_options = sorted(set(unsupported_options))
        options = ", ".join(self.unsupported_options)
        super().__init__(f"unsupported Pi options: {options}")


def validate_pi_options(
    model,
    models_config=None,
    lobster=None,
    thinking=None,
    serper_api_key="",
):
    unsupported = []
    provider, _, model_id = (
        model.partition("/") if isinstance(model, str) else ("", "", "")
    )
    if provider not in PI_PROVIDER_ENV or not model_id.strip():
        unsupported.append("--model")
    if models_config is not None:
        unsupported.append("--models-config")
    if isinstance(lobster, dict):
        for name in ("workspace", "name", "env"):
            if lobster.get(name):
                unsupported.append(f"--lobster-{name}")
    if thinking is not None and thinking not in PI_THINKING_LEVELS:
        unsupported.append("--thinking")
    if unsupported:
        raise PiConfigurationError(unsupported)


def resolve_pi_credentials(model, env=None):
    """Return (base_url, api_key) for the model's provider from the environment."""
    import os

    validate_pi_options(model)
    env = os.environ if env is None else env
    provider = model.split("/", 1)[0]
    url_key, api_key_key = PI_PROVIDER_ENV[provider]
    return env.get(url_key, ""), env.get(api_key_key, "")


def build_pi_models_config(model, base_url, api_key, supports_images=False):
    validate_pi_options(model)
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Pi base URL must not be empty")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("Pi API key must not be empty")
    normalized_url = base_url.rstrip("/")
    if not normalized_url.endswith("/v1"):
        normalized_url = f"{normalized_url}/v1"
    provider, model_id = model.split("/", 1)
    inputs = ["text", "image"] if supports_images else ["text"]
    model_entry = {
        "id": model_id,
        "name": model_id,
        "input": inputs,
    }
    model_entry.update(PI_PROVIDER_MODEL_EXTRAS.get(provider, {}))
    return {
        "providers": {
            provider: {
                "baseUrl": normalized_url,
                "apiKey": api_key,
                "api": "openai-completions",
                "models": [model_entry],
            }
        }
    }


def build_pi_command(
    model,
    prompt,
    timeout_seconds,
    session_dir,
    agent_dir,
    thinking=None,
    launcher="/usr/local/bin/pi_launcher.py",
):
    validate_pi_options(model, thinking=thinking)
    command = [
        str(launcher),
        "--timeout-seconds",
        str(timeout_seconds),
        "--agent-dir",
        str(agent_dir),
        "--",
        "pi",
        "--print",
        "--mode",
        "json",
        "--model",
        model,
        "--session-dir",
        str(session_dir),
    ]
    if thinking is not None:
        command.extend(("--thinking", thinking))
    command.append(prompt)
    return command


def write_pi_models_config(path, config):
    path = Path(path)
    atomic_write_json(path, config)
    return path


def pi_runtime_env(session_dir, agent_dir):
    return {
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "PI_CODING_AGENT_SESSION_DIR": str(session_dir),
    }


class PiHarnessAdapter:
    def __init__(self, launcher="/usr/local/bin/pi_launcher.py"):
        self.launcher = launcher

    def validate(self, model, **options):
        return validate_pi_options(model, **options)

    def models_config(self, model, base_url, api_key, supports_images=False):
        return build_pi_models_config(model, base_url, api_key, supports_images)

    def command(
        self,
        model,
        prompt,
        timeout_seconds,
        session_dir,
        agent_dir,
        thinking=None,
    ):
        return build_pi_command(
            model,
            prompt,
            timeout_seconds,
            session_dir,
            agent_dir,
            thinking,
            self.launcher,
        )

    def write_models_config(self, path, config):
        return write_pi_models_config(path, config)

    def runtime_env(self, session_dir, agent_dir):
        return pi_runtime_env(session_dir, agent_dir)
