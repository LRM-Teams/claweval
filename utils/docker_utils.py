from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DOCKER_IMAGE = os.environ.get("DOCKER_IMAGE", "wildclawbench-ubuntu:v0.4")
TMP_WORKSPACE = os.environ.get("TMP_WORKSPACE", "/tmp_workspace")
PI_ROOT = f"{TMP_WORKSPACE}/.pi"
PI_AGENT_DIR = f"{PI_ROOT}/agent"
PI_SESSION_DIR = f"{PI_AGENT_DIR}/sessions"

BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")
JINA_KEY = os.environ.get("JINA_KEY", "")


def remove_container(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def start_container(
    task_id: str,
    workspace_path: str,
    extra_env: str = "",
    tmp_path: str = "",
    lobster_env: list[str] | None = None,
) -> None:
    required_env = {
        "SERPER_API_KEY": SERPER_API_KEY,
        "JINA_KEY": JINA_KEY,
    }
    missing_env = [key for key, value in required_env.items() if not value]
    if missing_env:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing_env)}"
        )

    proxy_http = os.environ.get("HTTP_PROXY_INNER", "")
    proxy_https = os.environ.get("HTTPS_PROXY_INNER", "")
    env_args = [
        "-e",
        f"http_proxy={proxy_http}",
        "-e",
        f"https_proxy={proxy_https}",
        "-e",
        f"HTTP_PROXY={proxy_http}",
        "-e",
        f"HTTPS_PROXY={proxy_https}",
        "-e",
        f"BRAVE_API_KEY={BRAVE_API_KEY}",
        "-e",
        "SERPER_API_KEY",
        "-e",
        "JINA_KEY",
        "-e",
        f"no_proxy={'' if not proxy_http else os.environ.get('NO_PROXY_INNER', '')}",
        "-e",
        "NODE_TLS_REJECT_UNAUTHORIZED=0",
    ]
    for line in extra_env.splitlines():
        key = line.strip()
        if not key or key.startswith("#"):
            continue
        if key in required_env:
            continue
        value = os.environ.get(key, "")
        env_args += ["-e", f"{key}={value}"]
        masked = (value[:4] + "***") if value else "(empty)"
        logger.info("[%s] Injecting env var: %s=%s", task_id, key, masked)
    for key in lobster_env or []:
        value = os.environ.get(key, "")
        if not value:
            logger.warning(
                "[%s] Lobster env key %s not found in environment, skipping",
                task_id,
                key,
            )
            continue
        env_args += ["-e", f"{key}={value}"]
        masked = value[:4] + "***"
        logger.info("[%s] Injecting lobster env: %s=%s", task_id, key, masked)

    cmd = [
        "docker",
        "run",
        "-d",
        "--network",
        "host",
        "--name",
        task_id,
        *env_args,
        "-v",
        f"{workspace_path}:/app:ro",
        DOCKER_IMAGE,
        "/bin/bash",
        "-c",
        "tail -f /dev/null",
    ]
    print(f"[{task_id}] Docker image: {DOCKER_IMAGE}")
    print(f"[{task_id}] Docker command: {' '.join(cmd)}")
    logger.info(
        "[%s] Starting container, mounting %s → /app (ro)", task_id, workspace_path
    )
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Container startup failed:\n{r.stderr}")
    logger.info("[%s] Container ID: %s", task_id, r.stdout.strip()[:12])

    if tmp_path and os.path.exists(tmp_path):
        mkdir_cmd = ["docker", "exec", task_id, "mkdir", "-p", "/tmp_workspace/tmp"]
        subprocess.run(mkdir_cmd, capture_output=True)

        cp_cmd = ["docker", "cp", f"{tmp_path}/.", f"{task_id}:/tmp_workspace/tmp/"]

        logger.info(
            "[%s] Copying temp files: %s → /tmp_workspace/tmp", task_id, tmp_path
        )
        cp_r = subprocess.run(cp_cmd, capture_output=True, text=True)

        if cp_r.returncode != 0:
            logger.error("[%s] File copy failed: %s", task_id, cp_r.stderr)
        else:
            logger.info("[%s] Temp file copy complete", task_id)


def setup_workspace(task_id: str, thinking: str | None = None) -> None:
    setup_shared_workspace(task_id)
    setup_openclaw_workspace(task_id, thinking=thinking)


def setup_shared_workspace(task_id: str) -> None:
    logger.info("[%s] Copying /app → %s", task_id, TMP_WORKSPACE)
    r = subprocess.run(
        [
            "docker",
            "exec",
            task_id,
            "/bin/bash",
            "-c",
            f"cp -r /app/. {TMP_WORKSPACE} && chmod -R u+w {TMP_WORKSPACE}",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Workspace copy failed:\n{r.stderr}")


def setup_openclaw_workspace(task_id: str, thinking: str | None = None) -> None:
    if thinking is not None:
        logger.info("[%s] Setting thinkingDefault to %s", task_id, thinking)
        thinking_result = subprocess.run(
            [
                "docker",
                "exec",
                task_id,
                "openclaw",
                "config",
                "set",
                "agents.defaults.thinkingDefault",
                thinking,
            ],
            capture_output=True,
            text=True,
        )
        if thinking_result.returncode != 0:
            raise RuntimeError(
                f"Failed to set thinkingDefault to {thinking}:\n{thinking_result.stderr}"
            )

    # Symlink OpenClaw workspace → TMP_WORKSPACE so the image tool's
    # media-local-roots check allows reading files under /tmp_workspace.
    subprocess.run(
        [
            "docker",
            "exec",
            task_id,
            "/bin/bash",
            "-c",
            f"rm -rf /root/.openclaw/workspace && ln -s {TMP_WORKSPACE} /root/.openclaw/workspace",
        ],
        capture_output=True,
        text=True,
    )


def setup_skills(task_id: str, skills: str, skills_path: str) -> None:
    for line in skills.splitlines():
        line = line.strip()
        if not line:
            continue
        subprocess.run(
            ["docker", "exec", task_id, "mkdir", "-p", f"/root/skills/{line}"],
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["docker", "cp", f"{skills_path}/{line}", f"{task_id}:/root/skills"],
            capture_output=True,
            text=True,
        )


def prepare_pi_run(task_id: str, pi_root: str = PI_ROOT) -> None:
    subprocess.run(
        [
            "docker",
            "exec",
            task_id,
            "mkdir",
            "-p",
            f"{pi_root}/agent/skills",
            f"{pi_root}/agent/sessions",
            f"{pi_root}/control",
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def copy_pi_skill(task_id: str, source: str, pi_root: str = PI_ROOT) -> None:
    result = subprocess.run(
        ["docker", "cp", source, f"{task_id}:{pi_root}/agent/skills"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Pi skill copy failed:\n{result.stderr}")


def copy_pi_models(task_id: str, models_config: dict, pi_root: str = PI_ROOT) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".json", delete=False
    ) as handle:
        json.dump(models_config, handle, indent=2)
        temporary = handle.name
    try:
        result = subprocess.run(
            ["docker", "cp", temporary, f"{task_id}:{pi_root}/agent/models.json"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"Pi models config copy failed:\n{result.stderr}")
    finally:
        Path(temporary).unlink(missing_ok=True)


def copy_pi_sessions(task_id: str, destination: str, pi_root: str = PI_ROOT) -> bool:
    Path(destination).mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["docker", "cp", f"{task_id}:{pi_root}/agent/sessions/.", destination],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def remove_pi_runtime_before_grading(
    task_id: str,
    pi_root: str = PI_ROOT,
) -> None:
    script = (
        "import shutil\n"
        "from pathlib import Path\n"
        f"source = Path({pi_root!r})\n"
        "if source.exists():\n"
        "    shutil.rmtree(source)\n"
    )
    result = subprocess.run(
        ["docker", "exec", task_id, "python3", "-c", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to remove Pi runtime before grading:\n{result.stderr}")


def inject_openclaw_models(task_id: str, models_config: dict) -> None:
    """Inject custom models into ~/.openclaw/openclaw.json."""
    container_tmp_path = "/tmp/openclaw_models.json"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".json", delete=False
    ) as tmp_file:
        json.dump(models_config, tmp_file, indent=2)
        tmp_file_path = tmp_file.name

    try:
        cp_r = subprocess.run(
            ["docker", "cp", tmp_file_path, f"{task_id}:{container_tmp_path}"],
            capture_output=True,
            text=True,
        )
        if cp_r.returncode != 0:
            raise RuntimeError(
                f"Failed to copy models config into container:\n{cp_r.stderr}"
            )

        inject_cmd = f"""python3 - <<'PY'
import json
import pathlib

config_path = pathlib.Path('/root/.openclaw/openclaw.json')
models_path = pathlib.Path('{container_tmp_path}')

config = json.loads(config_path.read_text()) if config_path.exists() else {{}}
models = json.loads(models_path.read_text())
config['models'] = models

config_path.write_text(json.dumps(config, indent=2))
PY"""
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Failed to inject models config:\n{r.stderr}")
    finally:
        Path(tmp_file_path).unlink(missing_ok=True)

    logger.info("[%s] Injected custom models config", task_id)


def run_warmup(task_id: str, warmup: str) -> None:
    """Execute warmup bash commands line by line inside the container (skip blank lines and comments)."""
    if not warmup.strip():
        return
    commands = [
        line.strip()
        for line in warmup.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not commands:
        return

    logger.info("[%s] Running warmup (%d commands)", task_id, len(commands))
    for cmd in commands:
        logger.info("[%s] warmup: %s", task_id, cmd)
        r = subprocess.run(
            ["docker", "exec", task_id, "/bin/bash", "-c", cmd],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"Warmup command failed: {cmd!r}\n{r.stderr}")


def run_background(task_id: str, bash_cmd: str, log_path: Path) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            "docker",
            "exec",
            task_id,
            "/bin/bash",
            "-c",
            f"cd {TMP_WORKSPACE} && {bash_cmd}",
        ],
        stdout=log_file,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    proc._log_file = log_file
    logger.info("[%s] Started process PID=%s → %s", task_id, proc.pid, log_path)
    return proc


def run_background_argv(
    task_id: str,
    argv: list[str],
    log_path: Path,
    cwd: str = TMP_WORKSPACE,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("w", encoding="utf-8")
    command = ["docker", "exec", "-w", cwd]
    for key, value in (env or {}).items():
        command.extend(("-e", f"{key}={value}"))
    command.extend((task_id, *argv))
    proc = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
    )
    proc._log_file = log_file
    logger.info("[%s] Started argv process PID=%s → %s", task_id, proc.pid, log_path)
    return proc


def close_proc_log(proc: subprocess.Popen) -> None:
    """Close the log file handle created by run_background."""
    log_file = getattr(proc, "_log_file", None)
    if log_file and not log_file.closed:
        log_file.close()


def collect_output_from_container(task_id: str, output_dir: Path) -> None:
    """Collect task output files from the container to output_dir/task_output/.

    Collection strategy:
      1. All files under /tmp/openclaw/ (agent session logs, etc.)
      2. Task output files under /tmp_workspace/results/
    """
    task_output_dir = output_dir / "task_output"
    task_output_dir.mkdir(parents=True, exist_ok=True)

    _copy_dir_from_container(task_id, "/tmp/openclaw/.", str(task_output_dir))

    results_out = task_output_dir / "workspace" / "results"
    results_out.mkdir(parents=True, exist_ok=True)
    ok = _copy_dir_from_container(
        task_id,
        f"{TMP_WORKSPACE}/results/.",
        str(results_out),
    )
    if not ok:
        logger.warning("[%s] results/ directory does not exist or is empty", task_id)


def inject_lobster_workspace(task_id: str, workspace_path: str) -> None:
    """Copy the entire lobster workspace into /root/ (the OpenClaw workspace in the image).

    This brings in everything: SOUL.md, USER.md, MEMORY.md, memory/, skills/, etc.
    """
    r = subprocess.run(
        ["docker", "cp", f"{workspace_path}/.", f"{task_id}:/root/"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        logger.error("[%s] Lobster workspace copy failed: %s", task_id, r.stderr)
    else:
        logger.info(
            "[%s] Lobster workspace copied: %s → /root/", task_id, workspace_path
        )


def _copy_dir_from_container(task_id: str, src: str, dest: str) -> bool:
    r = subprocess.run(
        ["docker", "cp", f"{task_id}:{src}", dest],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        logger.info("[%s] Collected container directory %s → %s", task_id, src, dest)
        return True
    return False
