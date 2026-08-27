from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import time
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.task_parser import parse_task_md
from utils.docker_utils import (
    remove_container,
    start_container,
    setup_shared_workspace,
    setup_openclaw_workspace,
    setup_skills,
    inject_openclaw_models,
    inject_lobster_workspace,
    run_warmup,
    run_background,
    run_background_argv,
    prepare_pi_run,
    copy_pi_skill,
    copy_pi_models,
    copy_pi_sessions,
    remove_pi_runtime_before_grading,
    close_proc_log,
    collect_output_from_container,
    TMP_WORKSPACE,
    PI_AGENT_DIR,
    PI_SESSION_DIR,
)
from utils.grading import (
    run_grading,
    format_scores,
    print_summary,
    print_global_summary,
    extract_usage_from_jsonl,
)
from utils.harness import HarnessKind, apply_price_override, detect_harness, empty_usage
from utils.harness_overlay import (
    apply_harness_overlay,
    load_system_appendix,
    overlay_summary,
    validate_overlay,
)
from utils.pi_harness import (
    build_pi_command,
    build_pi_models_config,
    pi_runtime_env,
    resolve_pi_credentials,
    validate_pi_options,
)
from utils.pi_session import (
    copy_session_bytes,
    extract_usage_from_jsonl as extract_pi_usage,
    select_session_candidate,
)
from utils.run_artifacts import (
    atomic_write_json,
    initialize_run_artifacts,
    write_harness_error,
)

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "18789"))
_port_counter = __import__("itertools").count(0)

ROOT_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT_DIR / os.environ.get("TASKS_SUBDIR", "tasks")
OUTPUT_DIR = ROOT_DIR / os.environ.get("OUTPUT_SUBDIR", "output")

DEFAULT_MODEL = os.environ.get(
    "DEFAULT_MODEL", "openrouter/anthropic/claude-sonnet-4.6"
)
DEFAULT_PARALLEL = int(os.environ.get("DEFAULT_PARALLEL", "1"))

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
ZHIZENGZENG_API_KEY = os.environ.get("ZHIZENGZENG_API_KEY", "")
ZHIZENGZENG_API_URL = os.environ.get(
    "ZHIZENGZENG_API_URL", "https://api.zhizengzeng.com/v1"
)

# Web search api for search tools
SERPER_API_KEY = os.environ.get("SERPER_API_KEY", "")

MODELS_API_KEY_PLACEHOLDER = "${MY_PROXY_API_KEY}"

ALL_CATEGORIES = [
    "01_Productivity_Flow",
    "02_Code_Intelligence",
    "03_Social_Interaction",
    "04_Search_Retrieval",
    "05_Creative_Synthesis",
    "06_Safety_Alignment",
]


def find_completed_run(
    category: str, task_id_ori: str, short_model: str, lobster_prefix: str = ""
) -> dict | None:
    """Check if a task has already been successfully completed.

    Scans output/<category>/<task_id_ori>/ for subdirectories matching the
    model prefix, then checks for a valid score.json.

    Returns a result dict (compatible with run_single_task return value) if
    a completed run is found, otherwise None.
    """
    task_output_dir = OUTPUT_DIR / category / task_id_ori
    if not task_output_dir.exists():
        return None

    prefix = f"{lobster_prefix}{short_model}_"
    candidates = []
    for d in task_output_dir.iterdir():
        if d.is_dir() and d.name.startswith(prefix):
            candidates.append(d)

    # Sort by directory name descending (newer timestamps first)
    candidates.sort(key=lambda p: p.name, reverse=True)

    for d in candidates:
        score_file = d / "score.json"
        if not score_file.exists():
            continue
        try:
            scores = json.loads(score_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if "error" in scores:
            continue
        if not isinstance(scores.get("overall_score"), (int, float)):
            continue

        # Valid completed run found — reconstruct result dict
        usage_file = d / "usage.json"
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "request_count": 0,
            "elapsed_time": 0.0,
        }
        if usage_file.exists():
            try:
                usage = json.loads(usage_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        result = {
            "task_id": f"{task_id_ori}_{d.name}",
            "scores": scores,
            "usage": usage,
            "error": None,
        }
        logger.info(
            "[%s] Resume: found completed run in %s (overall_score=%.2f)",
            task_id_ori,
            d.name,
            scores["overall_score"],
        )
        return result

    return None


def grade_the_task(
    task_id: str,
    workspace_path: str,
    output_dir: Path,
    task: dict,
    result: dict,
    transcript_path: Path | None = None,
):
    gt_host = os.path.join(workspace_path, "gt")
    if os.path.isdir(gt_host):
        r_gt = subprocess.run(
            ["docker", "cp", gt_host, f"{task_id}:{TMP_WORKSPACE}/gt"],
            capture_output=True,
            text=True,
        )
        if r_gt.returncode != 0:
            logger.warning("[%s] gt directory copy failed: %s", task_id, r_gt.stderr)
        else:
            logger.info(
                "[%s] gt directory copied to container %s/gt", task_id, TMP_WORKSPACE
            )

    if not result.get("error") and task.get("automated_checks"):
        try:
            scores = run_grading(
                task_id=task_id,
                automated_checks=task["automated_checks"],
                output_dir=output_dir,
                transcript_path=transcript_path,
            )
            result["scores"] = scores
            print(format_scores(task_id, scores))
            logger.info("[%s] Grading complete", task_id)
        except Exception as exc:
            logger.error("[%s] Grading failed: %s", task_id, exc)
            result["scores"] = {"error": str(exc)}
    elif not task.get("automated_checks"):
        logger.info("[%s] No Automated Checks, skipping grading", task_id)

    return result


def cal_cost(
    task_id: str,
    output_dir: Path,
    result: dict,
    elapsed_time: float,
    input_price: float = None,
    output_price: float = None,
    cache_read_price: float = None,
):
    transcript_container = "/root/.openclaw/agents/main/sessions/chat.jsonl"
    transcript_host = output_dir / "chat.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    r_cp = subprocess.run(
        ["docker", "cp", f"{task_id}:{transcript_container}", str(transcript_host)],
        capture_output=True,
        text=True,
    )
    if r_cp.returncode == 0 and transcript_host.exists():
        usage = extract_usage_from_jsonl(transcript_host)
    else:
        logger.warning("[%s] Transcript copy failed: %s", task_id, r_cp.stderr.strip())
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "request_count": 0,
        }
    # Override cost if per-token prices are provided
    if input_price is not None and output_price is not None:
        cr_price = cache_read_price if cache_read_price is not None else 0.0
        computed_cost = (
            usage["input_tokens"] * input_price
            + usage["cache_read_tokens"] * cr_price
            + usage["output_tokens"] * output_price
        ) / 1_000_000
        usage["cost_usd"] = round(computed_cost, 6)
    usage["elapsed_time"] = round(elapsed_time, 2)
    result["usage"] = usage
    if usage["request_count"] > 0:
        logger.info(
            "[%s] Token usage — input:%d output:%d cache_read:%d total:%d cost:$%.4f",
            task_id,
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cache_read_tokens"],
            usage["total_tokens"],
            usage["cost_usd"],
        )
    usage_path = output_dir / "usage.json"
    usage_path.write_text(
        json.dumps(usage, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("[%s] Usage written to → %s", task_id, usage_path)
    return result


def collect_task_output(task_id: str, output_dir: Path) -> None:
    """Collect task output files from the container to output_dir/task_output/."""
    try:
        collect_output_from_container(task_id, output_dir)
    except Exception as exc:
        logger.warning("[%s] Failed to collect task output: %s", task_id, exc)


def collect_pi_usage(
    task_id: str,
    output_dir: Path,
    run_marker: str,
    elapsed_time: float,
    result: dict,
    input_price: float = None,
    output_price: float = None,
    cache_read_price: float = None,
) -> dict:
    candidates_dir = output_dir / "task_output" / "harness" / "pi-sessions"
    usage = empty_usage(round(elapsed_time, 2))
    try:
        if not copy_pi_sessions(task_id, str(candidates_dir)):
            raise RuntimeError("failed to copy isolated Pi sessions")
        selected = select_session_candidate(
            candidates_dir,
            expected_cwd=TMP_WORKSPACE,
            run_marker=run_marker,
        )
        copy_session_bytes(selected, output_dir / "chat.jsonl")
        usage = extract_pi_usage(output_dir / "chat.jsonl")
        usage["elapsed_time"] = round(elapsed_time, 2)
        apply_price_override(usage, input_price, output_price, cache_read_price)
    except Exception as exc:
        write_harness_error(output_dir, "transcript_discovery", str(exc))
        logger.warning("[%s] Pi transcript collection failed: %s", task_id, exc)
    atomic_write_json(output_dir / "usage.json", usage)
    result["usage"] = usage
    return result


def set_model(task_id: str, model: str) -> None:
    r = subprocess.run(
        [
            "docker",
            "exec",
            task_id,
            "/bin/bash",
            "-c",
            f"openclaw models set '{model}'",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Model setup failed:\n{r.stderr}")
    logger.info("[%s] Model set: %s", task_id, model)


def inject_tools_and_plugins(task_id: str) -> None:
    """Inject tools and plugins configuration into /root/.openclaw/openclaw.json."""
    config_path = "/root/.openclaw/openclaw.json"

    tools_config = {"web": {"search": {"enabled": True, "provider": "serper"}}}

    plugins_config = {
        "entries": {
            "serper": {
                "config": {
                    "webSearch": {
                        "mode": "default",
                        "gl": "world",
                        "hl": "en",
                        "pythonCommand": "python3",
                        "apiKey": f"{SERPER_API_KEY}",
                    }
                },
                "enabled": True,
            },
            "browser": {"enabled": True},
        }
    }

    # Escape JSON strings for shell embedding
    tools_config_json = json.dumps(tools_config).replace('"', '\\"')
    plugins_config_json = json.dumps(plugins_config).replace('"', '\\"')

    inject_cmd = (
        f'python3 -c "'
        f"import json, pathlib; "
        f"p = pathlib.Path('{config_path}'); "
        f"d = json.loads(p.read_text()) if p.exists() else {{}}; "
        f"d['tools'] = json.loads('{tools_config_json}'); "
        f"d['plugins'] = json.loads('{plugins_config_json}'); "
        f'p.write_text(json.dumps(d, indent=2))"'
    )

    r = subprocess.run(
        ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        logger.error(
            "[%s] Failed to inject tools/plugins config: %s", task_id, r.stderr
        )
    else:
        logger.info("[%s] Injected tools and plugins configuration", task_id)
    logger.info(
        "[%s] Web search tool provider has been sustituded in openclaw.json, now it is Serper",
        task_id,
    )


def load_models_config(models_config_path: Path) -> dict:
    raw_config = models_config_path.read_text(encoding="utf-8")
    proxy_api_key = os.environ.get("MY_PROXY_API_KEY")
    if MODELS_API_KEY_PLACEHOLDER in raw_config and not proxy_api_key:
        raise ValueError(
            "MY_PROXY_API_KEY must be set to a non-empty value when models config uses ${MY_PROXY_API_KEY}"
        )

    expanded_config = raw_config.replace(
        MODELS_API_KEY_PLACEHOLDER,
        proxy_api_key or "",
    )
    parsed_models_config = json.loads(expanded_config)
    if not isinstance(parsed_models_config, dict):
        raise ValueError(f"Models config must be a JSON object: {models_config_path}")
    return parsed_models_config


def run_single_task(
    task: dict,
    model: str,
    lobster: dict | None = None,
    thinking: str | None = None,
    models_config: dict | None = None,
    input_price: float = None,
    output_price: float = None,
    cache_read_price: float = None,
    evolved_harness: str | None = None,
) -> dict:
    task_id_ori = task["task_id"]

    overlay_dir = None
    overlay_appendix = None
    if evolved_harness:
        # Fail fast (before any container starts) if the overlay is invalid.
        overlay_dir = validate_overlay(evolved_harness)
        overlay_appendix = load_system_appendix(overlay_dir)

    workspace_path = task["workspace_path"]
    timeout_seconds = task["timeout_seconds"]
    skills = task["skills"]
    skills_path = task["skills_path"]
    system_prompt = f"You are an expert in a restricted, non-interactive environment. Solve the task efficiently before the timeout ({timeout_seconds}s). Run all processes in the foreground without user input or background services. Provide a complete, functional solution in a single pass with no placeholders. \n"
    prompt = system_prompt + task["prompt"]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    run_id = uuid.uuid4().hex[:6]
    _m = re.match(r"(\d+)_.*?(task_\d+)", task_id_ori)
    short_task_id = f"{_m.group(1)}_{_m.group(2)}" if _m else task_id_ori
    short_model = re.sub(r"[^a-zA-Z0-9.\-_]", "_", model.rsplit("/", 1)[-1])
    lobster_prefix = f"{lobster['name']}_" if lobster else ""
    suffix = f"{lobster_prefix}{short_model}_{timestamp}_{run_id}"
    task_id = f"{short_task_id}_{lobster_prefix}{short_model}_{timestamp}_{run_id}"

    output_dir = OUTPUT_DIR / task["category"] / f"{task_id_ori}" / f"{suffix}"
    initialize_run_artifacts(output_dir)

    result = {"task_id": task_id, "scores": {}, "error": None, "output_dir": str(output_dir)}
    gateway_proc = None
    agent_proc = None
    elapsed_time = float(timeout_seconds)
    harness = None
    container_started = False
    agent_started = False
    grading_eligible = False
    stage = "container_startup"
    run_marker = f"WILDCLAW_RUN_MARKER:{run_id}"

    try:
        exec_path = os.path.join(workspace_path, "exec")
        tmp_path = os.path.join(workspace_path, "tmp")
        os.makedirs(exec_path, exist_ok=True)
        start_container(
            task_id,
            exec_path,
            extra_env=task.get("env", ""),
            tmp_path=tmp_path,
            lobster_env=lobster.get("env") if lobster else None,
        )
        container_started = True
        stage = "workspace_setup"
        setup_shared_workspace(task_id)
        stage = "harness_detection"
        harness = detect_harness(task_id)

        if harness is HarnessKind.OPENCLAW:
            if overlay_dir is not None:
                raise RuntimeError(
                    "--evolved-harness is only supported for the Pi harness"
                )
            if lobster:
                inject_lobster_workspace(task_id, lobster["workspace"])
            setup_openclaw_workspace(task_id, thinking=thinking)
            stage = "skill_injection"
            setup_skills(task_id, skills, skills_path)
            stage = "warmup"
            run_warmup(task_id, task.get("warmup", ""))
            stage = "configuration"
            if models_config:
                inject_openclaw_models(task_id, models_config)
            set_model(task_id, model)
            if OPENROUTER_API_KEY:
                auth_profile_path = (
                    "/root/.openclaw/agents/main/agent/auth-profiles.json"
                )
                inject_cmd = (
                    f"python3 -c \"import json, pathlib; p = pathlib.Path('{auth_profile_path}'); "
                    f"d = json.loads(p.read_text()) if p.exists() else {{'version':1,'profiles':{{}}}}; "
                    f"d.setdefault('profiles',{{}})['openrouter:default'] = {{'type':'api_key','provider':'openrouter','key':'{OPENROUTER_API_KEY}'}}; "
                    f'p.write_text(json.dumps(d, indent=2))"'
                )
                subprocess.run(
                    ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
                    capture_output=True,
                    text=True,
                )
                logger.info(
                    "[%s] Injected OPENROUTER_API_KEY into auth-profiles.json", task_id
                )
            elif ZHIZENGZENG_API_KEY:
                config_path = "/root/.openclaw/openclaw.json"
                model_id = (
                    model.split("/", 1)[1] if model.startswith("vllm/") else model
                )
                vllm_provider = {
                    "baseUrl": ZHIZENGZENG_API_URL,
                    "apiKey": ZHIZENGZENG_API_KEY,
                    "api": "openai-completions",
                    "models": [
                        {"id": model_id, "name": model_id, "input": ["text", "image"]}
                    ],
                }
                provider_json = json.dumps(vllm_provider).replace('"', '\\"')
                inject_cmd = (
                    f"python3 -c \"import json, pathlib; p = pathlib.Path('{config_path}'); "
                    f"d = json.loads(p.read_text()) if p.exists() else {{}}; "
                    f"d.setdefault('models', {{}}).setdefault('providers', {{}})['vllm'] = json.loads('{provider_json}'); "
                    f"d.setdefault('agents', {{}}).setdefault('defaults', {{}}).setdefault('models', {{}})['vllm/*'] = {{}}; "
                    f"d['agents']['defaults']['model']['primary'] = '{model}'; "
                    f'p.write_text(json.dumps(d, indent=2))"'
                )
                subprocess.run(
                    ["docker", "exec", task_id, "/bin/bash", "-c", inject_cmd],
                    capture_output=True,
                    text=True,
                )
                logger.info(
                    "[%s] Injected vllm provider config for model: %s", task_id, model
                )
            subprocess.run(
                [
                    "docker",
                    "exec",
                    task_id,
                    "/bin/bash",
                    "-c",
                    f"openclaw config set agents.defaults.imageModel.primary '{model}'",
                ],
                capture_output=True,
                text=True,
            )
            logger.info("[%s] imageModel set: %s", task_id, model)
            if SERPER_API_KEY:
                inject_tools_and_plugins(task_id)
            stage = "service_startup"
            port = GATEWAY_PORT + next(_port_counter)
            gateway_proc = run_background(
                task_id,
                bash_cmd=f"export OPENROUTER_API_KEY='{OPENROUTER_API_KEY}' && openclaw gateway --port {port}",
                log_path=output_dir / "gateway.log",
            )
            time.sleep(2)
            safe_prompt = prompt.replace("'", "'\\''")
            stage = "agent_execution"
            agent_proc = run_background(
                task_id,
                bash_cmd=f"openclaw agent --session-id chat --timeout {timeout_seconds} --message '{safe_prompt}'",
                log_path=output_dir / "agent.log",
            )
        else:
            stage = "configuration"
            validate_pi_options(
                model,
                models_config=models_config,
                lobster=lobster,
                thinking=thinking,
                serper_api_key=SERPER_API_KEY,
            )
            base_url, api_key = resolve_pi_credentials(model)
            pi_models = build_pi_models_config(model, base_url, api_key)
            prepare_pi_run(task_id)
            stage = "skill_injection"
            for skill in (line.strip() for line in skills.splitlines()):
                if skill:
                    copy_pi_skill(task_id, str(Path(skills_path) / skill))
            stage = "warmup"
            run_warmup(task_id, task.get("warmup", ""))
            stage = "configuration"
            copy_pi_models(task_id, pi_models)
            if overlay_dir is not None:
                stage = "harness_overlay"
                applied = apply_harness_overlay(task_id, overlay_dir)
                audit = overlay_summary(overlay_dir)
                audit["applied"] = applied
                atomic_write_json(
                    output_dir / "task_output" / "harness" / "overlay.json", audit
                )
                if overlay_appendix:
                    # Stored separately from the task prompt for auditability.
                    (output_dir / "system_appendix.md").write_text(
                        overlay_appendix + "\n", encoding="utf-8"
                    )
                stage = "configuration"
            pi_prompt = f"[{run_marker}]\n{prompt}"
            if overlay_appendix:
                pi_prompt = (
                    f"{pi_prompt}\n\n## Additional Instructions\n{overlay_appendix}"
                )
            command = build_pi_command(
                model,
                pi_prompt,
                timeout_seconds,
                PI_SESSION_DIR,
                PI_AGENT_DIR,
                thinking=thinking,
            )
            stage = "agent_execution"
            agent_proc = run_background_argv(
                task_id,
                command,
                output_dir / "agent.log",
                cwd=TMP_WORKSPACE,
                env=pi_runtime_env(PI_SESSION_DIR, PI_AGENT_DIR),
            )

        agent_started = True
        grading_eligible = True
        start_time = time.perf_counter()
        try:
            host_timeout = (
                timeout_seconds + 10 if harness is HarnessKind.PI else timeout_seconds
            )
            agent_proc.wait(timeout=host_timeout)
            elapsed_time = time.perf_counter() - start_time
            if agent_proc.returncode != 0:
                write_harness_error(
                    output_dir,
                    "agent_execution",
                    f"agent exited with code {agent_proc.returncode}",
                )
        except subprocess.TimeoutExpired:
            elapsed_time = timeout_seconds
            agent_proc.kill()
            agent_proc.wait()
            write_harness_error(output_dir, "agent_execution", "agent timed out")
        logger.info("[%s] Agent exit code: %s", task_id, agent_proc.returncode)

    except Exception as exc:
        logger.error("[%s] Execution error: %s", task_id, exc)
        result["error"] = str(exc)
        details = None
        if hasattr(exc, "unsupported_options"):
            details = {"unsupported_options": exc.unsupported_options}
        write_harness_error(output_dir, stage, str(exc), details)

    finally:
        if agent_started:
            if harness is HarnessKind.PI:
                result = collect_pi_usage(
                    task_id,
                    output_dir,
                    run_marker,
                    elapsed_time,
                    result,
                    input_price,
                    output_price,
                    cache_read_price,
                )
            else:
                result = cal_cost(
                    task_id,
                    output_dir,
                    result,
                    elapsed_time,
                    input_price=input_price,
                    output_price=output_price,
                    cache_read_price=cache_read_price,
                )
            collect_task_output(task_id, output_dir)
            if grading_eligible and harness is HarnessKind.PI:
                try:
                    remove_pi_runtime_before_grading(task_id)
                except Exception as exc:
                    logger.error(
                        "[%s] Failed to prepare Pi workspace for grading: %s",
                        task_id,
                        exc,
                    )
                    write_harness_error(output_dir, "grading_setup", str(exc))
                    result["scores"] = {"error": str(exc)}
                    grading_eligible = False
            if grading_eligible:
                grading_transcript = (
                    output_dir / "chat.jsonl"
                    if harness is HarnessKind.PI
                    else None
                )
                result = grade_the_task(
                    task_id,
                    workspace_path,
                    output_dir,
                    task,
                    result,
                    grading_transcript,
                )
        if gateway_proc is not None:
            try:
                gateway_proc.terminate()
                gateway_proc.wait(timeout=5)
            except Exception:
                try:
                    gateway_proc.kill()
                    gateway_proc.wait()
                except Exception:
                    pass
        for process in (gateway_proc, agent_proc):
            if process is not None:
                try:
                    close_proc_log(process)
                except Exception:
                    pass
        if container_started:
            remove_container(task_id)
            logger.info("[%s] Container cleaned up", task_id)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ClawBench evaluation entry point",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single task
  python eval/run.py --task tasks/01_Productivity_Flow/task_23_arxiv_digest.md

  # Entire category (sequential)
  python eval/run.py --category 01_Productivity_Flow

  # Entire category (4 containers in parallel)
  python eval/run.py --category 01_Productivity_Flow --parallel 4

  # Specify model
  python eval/run.py --category 01_Productivity_Flow -m openrouter/google/gemini-2-5-pro
        """,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--task", "-t", help="Path to a single task.md file")
    mode.add_argument(
        "--category",
        "-c",
        help="Category name, e.g. 01_Productivity_Flow, 02_Code_Intelligence, 03_Social_Interaction, 04_Search_Retrieval, 05_Creative_Synthesis, 06_Safety_Alignment",
    )

    parser.add_argument(
        "--model",
        "-m",
        default=DEFAULT_MODEL,
        help=f"Model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--parallel",
        "-p",
        type=int,
        default=DEFAULT_PARALLEL,
        metavar="N",
        help="Number of parallel containers (default: 1, i.e. sequential)",
    )
    parser.add_argument(
        "--lobster-name",
        default=None,
        help="Lobster name (used in output directory for comparison)",
    )
    parser.add_argument(
        "--lobster-workspace",
        default=None,
        help="Path to a personal OpenClaw workspace (contains SOUL.md, USER.md, etc.)",
    )
    parser.add_argument(
        "--lobster-env",
        default=None,
        help="Comma-separated env var names for skills that need API keys (e.g. GEMINI_API_KEY,FIRECRAWL_API_KEY)",
    )
    parser.add_argument(
        "--models-config",
        default=None,
        help="Path to a JSON file that will replace the top-level models field in ~/.openclaw/openclaw.json before each task",
    )
    parser.add_argument(
        "--thinking",
        default=None,
        help="Thinking/reasoning level for the model (default: high)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help="Skip tasks that already have a valid score.json for this model (resume interrupted runs)",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for results (default: output/)",
    )
    parser.add_argument(
        "--input-price",
        type=float,
        default=None,
        help="Input token price in USD per million tokens (overrides cost from provider)",
    )
    parser.add_argument(
        "--output-price",
        type=float,
        default=None,
        help="Output token price in USD per million tokens (overrides cost from provider)",
    )
    parser.add_argument(
        "--cache-read-price",
        type=float,
        default=None,
        help="Cache read token price in USD per million tokens (overrides cost from provider)",
    )
    parser.add_argument(
        "--evolved-harness",
        default=None,
        help="Path to an evolved harness overlay for this task (Pi only), e.g. "
        "evolved/<task_id>/champion or a directory containing overlay/",
    )

    args = parser.parse_args()

    if args.evolved_harness and args.category:
        logger.error("--evolved-harness is per-task only; use it with --task")
        sys.exit(1)
    if args.evolved_harness:
        try:
            validate_overlay(args.evolved_harness)
        except ValueError as exc:
            logger.error("Invalid evolved harness: %s", exc)
            sys.exit(1)

    # Override OUTPUT_DIR if provided
    global OUTPUT_DIR
    if args.output_dir:
        OUTPUT_DIR = Path(args.output_dir).expanduser().resolve()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Using custom output directory: %s", OUTPUT_DIR)

    models_config = None
    if args.models_config:
        models_config_path = Path(args.models_config).expanduser()
        if not models_config_path.is_file():
            logger.error("Models config not found: %s", models_config_path)
            sys.exit(1)
        try:
            models_config = load_models_config(models_config_path.resolve())
        except (ValueError, json.JSONDecodeError) as exc:
            logger.error("Invalid models config: %s", exc)
            sys.exit(1)

    lobster = None
    if args.lobster_workspace:
        if not args.lobster_name:
            logger.error("--lobster-workspace requires --lobster-name")
            sys.exit(1)
        workspace = Path(args.lobster_workspace).expanduser()
        if not workspace.is_dir():
            logger.error("Lobster workspace not found: %s", workspace)
            sys.exit(1)
        env_keys = (
            [k.strip() for k in args.lobster_env.split(",") if k.strip()]
            if args.lobster_env
            else []
        )
        lobster = {
            "name": args.lobster_name,
            "workspace": str(workspace.resolve()),
            "env": env_keys,
        }
        logger.info(
            "Lobster mode: %s (workspace=%s, env_keys=%s)",
            lobster["name"],
            lobster["workspace"],
            lobster["env"],
        )

    if args.task:
        task_file = Path(args.task)
        if not task_file.exists():
            logger.error("File not found: %s", task_file)
            sys.exit(1)
        task = parse_task_md(task_file)
        logger.info("Single task mode: %s", task["task_id"])

        if args.resume:
            short_model = re.sub(
                r"[^a-zA-Z0-9.\-_]", "_", args.model.rsplit("/", 1)[-1]
            )
            lobster_prefix = f"{lobster['name']}_" if lobster else ""
            cached = find_completed_run(
                task["category"], task["task_id"], short_model, lobster_prefix
            )
            if cached:
                logger.info("[%s] Skipped (resume): already completed", task["task_id"])
                return

        run_single_task(
            task,
            args.model,
            lobster=lobster,
            models_config=models_config,
            thinking=args.thinking,
            input_price=args.input_price,
            output_price=args.output_price,
            cache_read_price=args.cache_read_price,
            evolved_harness=args.evolved_harness,
        )
        return
    if args.category.lower() == "all":
        categories = ALL_CATEGORIES
    else:
        categories = [args.category]

    all_results: list[dict] = []
    safe_model_name = re.sub(r"[^a-zA-Z0-9.\-_]", "_", args.model)

    for category in categories:
        category_dir = TASKS_DIR / category
        if not category_dir.exists():
            logger.error("Category directory not found: %s", category_dir)
            continue

        task_files = sorted(category_dir.glob("*task_*.md"))
        if not task_files:
            logger.error("No task_*.md files found in: %s", category_dir)
            continue

        logger.info(
            "Category: %s, %d tasks, parallelism: %d",
            category,
            len(task_files),
            args.parallel,
        )

        tasks = []
        for tf in task_files:
            try:
                tasks.append(parse_task_md(tf))
            except Exception as exc:
                logger.error("Parse failed %s: %s", tf, exc)

        if not tasks:
            continue

        # --resume: filter out already-completed tasks
        short_model = re.sub(r"[^a-zA-Z0-9.\-_]", "_", args.model.rsplit("/", 1)[-1])
        lobster_prefix = f"{lobster['name']}_" if lobster else ""
        cached_results: list[dict] = []
        pending_tasks: list[dict] = []

        if args.resume:
            for task in tasks:
                cached = find_completed_run(
                    task["category"], task["task_id"], short_model, lobster_prefix
                )
                if cached:
                    cached_results.append(cached)
                else:
                    pending_tasks.append(task)
            if cached_results:
                logger.info(
                    "Category %s: %d tasks skipped (resume), %d remaining",
                    category,
                    len(cached_results),
                    len(pending_tasks),
                )
        else:
            pending_tasks = tasks

        results: list[dict] = []
        if pending_tasks:
            if args.parallel <= 1:
                # Sequential mode with progress bar
                for task in tqdm(
                    pending_tasks, desc=f"{category} (sequential)", unit="task"
                ):
                    results.append(
                        run_single_task(
                            task,
                            args.model,
                            lobster=lobster,
                            models_config=models_config,
                            thinking=args.thinking,
                            input_price=args.input_price,
                            output_price=args.output_price,
                            cache_read_price=args.cache_read_price,
                        )
                    )
            else:
                # Parallel mode with progress bar
                with ThreadPoolExecutor(max_workers=args.parallel) as pool:
                    futures = {
                        pool.submit(
                            run_single_task,
                            task,
                            args.model,
                            lobster,
                            args.thinking,
                            models_config,
                            args.input_price,
                            args.output_price,
                            args.cache_read_price,
                        ): task["task_id"]
                        for task in pending_tasks
                    }
                    with tqdm(
                        total=len(futures),
                        desc=f"{category} (parallel={args.parallel})",
                        unit="task",
                    ) as pbar:
                        for future in as_completed(futures):
                            tid = futures[future]
                            try:
                                results.append(future.result())
                            except Exception as exc:
                                logger.error("[%s] Thread exception: %s", tid, exc)
                                results.append(
                                    {"task_id": tid, "scores": {}, "error": str(exc)}
                                )
                            pbar.update(1)

        # Merge cached + new results for summary
        all_category_results = cached_results + results
        summary_label = (
            f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        )
        print_summary(all_category_results, category, OUTPUT_DIR, summary_label)
        all_results.extend(all_category_results)

    if len(categories) > 1 and all_results:
        summary_label = (
            f"{lobster['name']}_{safe_model_name}" if lobster else safe_model_name
        )
        print_global_summary(all_results, OUTPUT_DIR, summary_label)


if __name__ == "__main__":
    main()
