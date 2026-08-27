import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import run_batch
from utils.harness import HarnessKind, empty_usage


class FakeProcess:
    def __init__(self, returncode=0, times_out=False):
        self.returncode = None if times_out else returncode
        self.times_out = times_out
        self.wait_calls = []
        self.killed = False
        self.terminated = False

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.times_out:
            self.times_out = False
            raise subprocess.TimeoutExpired("agent", timeout)
        if self.returncode is None:
            self.returncode = -9 if self.killed else -15 if self.terminated else 0
        return self.returncode

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True


def task(tmp_path, skills=""):
    workspace = tmp_path / "workspace"
    (workspace / "exec").mkdir(parents=True)
    skills_path = tmp_path / "skills"
    skills_path.mkdir()
    return {
        "task_id": "01_Productivity_Flow_task_1_demo",
        "category": "01_Productivity_Flow",
        "workspace_path": str(workspace),
        "prompt": "Create the requested result.",
        "timeout_seconds": 7,
        "env": "",
        "skills": skills,
        "skills_path": str(skills_path),
        "warmup": "prepare-data",
        "automated_checks": "def grade(transcript, workspace_path): return {'overall_score': 1.0}",
    }


def run_dir(output_root):
    candidates = list(
        (
            output_root / "01_Productivity_Flow" / "01_Productivity_Flow_task_1_demo"
        ).iterdir()
    )
    assert len(candidates) == 1
    return candidates[0]


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_openclaw_route_preserves_setup_and_collection_order(tmp_path, monkeypatch):
    events = []
    grading_transcripts = []
    gateway = FakeProcess()
    agent = FakeProcess()
    monkeypatch.setattr(run_batch, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(run_batch, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(run_batch, "ZHIZENGZENG_API_KEY", "")
    monkeypatch.setattr(run_batch, "SERPER_API_KEY", "")
    monkeypatch.setattr(run_batch.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        run_batch, "start_container", lambda *args, **kwargs: events.append("start")
    )
    monkeypatch.setattr(
        run_batch,
        "setup_shared_workspace",
        lambda task_id: events.append("shared"),
        raising=False,
    )
    monkeypatch.setattr(
        run_batch,
        "detect_harness",
        lambda task_id: events.append("detect") or HarnessKind.OPENCLAW,
        raising=False,
    )
    monkeypatch.setattr(
        run_batch, "inject_lobster_workspace", lambda *args: events.append("lobster")
    )
    monkeypatch.setattr(
        run_batch,
        "setup_openclaw_workspace",
        lambda task_id, thinking=None: events.append(("openclaw_workspace", thinking)),
        raising=False,
    )
    monkeypatch.setattr(
        run_batch, "setup_skills", lambda *args: events.append("skills")
    )
    monkeypatch.setattr(run_batch, "run_warmup", lambda *args: events.append("warmup"))
    monkeypatch.setattr(
        run_batch, "inject_openclaw_models", lambda *args: events.append("models")
    )
    monkeypatch.setattr(run_batch, "set_model", lambda *args: events.append("model"))
    monkeypatch.setattr(
        run_batch.subprocess, "run", lambda *args, **kwargs: completed()
    )

    def background(task_id, bash_cmd, log_path):
        events.append(("background", bash_cmd, Path(log_path).name))
        return gateway if "gateway" in bash_cmd else agent

    monkeypatch.setattr(run_batch, "run_background", background)
    monkeypatch.setattr(
        run_batch,
        "cal_cost",
        lambda *args, **kwargs: events.append("openclaw_transcript") or args[2],
    )
    monkeypatch.setattr(
        run_batch, "collect_task_output", lambda *args: events.append("collect")
    )
    monkeypatch.setattr(
        run_batch,
        "remove_pi_runtime_before_grading",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("OpenClaw must not isolate Pi runtime")
        ),
    )

    def grade(*args):
        events.append("grade")
        grading_transcripts.append(args[5])
        args[4]["scores"] = {"overall_score": 1.0}
        return args[4]

    monkeypatch.setattr(run_batch, "grade_the_task", grade)
    monkeypatch.setattr(
        run_batch, "close_proc_log", lambda proc: events.append(("close", proc))
    )
    monkeypatch.setattr(
        run_batch, "remove_container", lambda task_id: events.append("remove")
    )

    result = run_batch.run_single_task(
        task(tmp_path),
        "openrouter/example/model",
        lobster={"name": "demo", "workspace": "/lobster", "env": []},
        thinking="high",
        models_config={"providers": {}},
    )

    assert events[:8] == [
        "start",
        "shared",
        "detect",
        "lobster",
        ("openclaw_workspace", "high"),
        "skills",
        "warmup",
        "models",
    ]
    backgrounds = [
        event
        for event in events
        if isinstance(event, tuple) and event[0] == "background"
    ]
    assert backgrounds[0][2] == "gateway.log"
    assert "openclaw gateway --port" in backgrounds[0][1]
    assert backgrounds[1][2] == "agent.log"
    assert "openclaw agent --session-id chat" in backgrounds[1][1]
    assert (
        events.index("openclaw_transcript")
        < events.index("collect")
        < events.index("grade")
    )
    assert gateway.terminated
    assert gateway.wait_calls
    assert ("close", gateway) in events
    assert ("close", agent) in events
    assert events[-1] == "remove"
    assert grading_transcripts == [None]
    assert result["scores"] == {"overall_score": 1.0}


def test_pi_timeout_uses_safe_argv_collects_persisted_session_and_still_grades(
    tmp_path, monkeypatch
):
    events = []
    grading_transcripts = []
    docker_commands = []
    copied_config = {}
    launched = {}
    agent = FakeProcess(times_out=True)
    requested_task = task(tmp_path, skills="browser-skill")
    (Path(requested_task["skills_path"]) / "browser-skill").mkdir()
    output_root = tmp_path / "output"
    monkeypatch.setattr(run_batch, "OUTPUT_DIR", output_root)
    monkeypatch.setenv("ZHIZENGZENG_API_URL", "https://models.example.test")
    monkeypatch.setenv("ZHIZENGZENG_API_KEY", "pi-super-secret")
    monkeypatch.setattr(
        run_batch, "start_container", lambda *args, **kwargs: events.append("start")
    )
    monkeypatch.setattr(
        run_batch,
        "setup_shared_workspace",
        lambda task_id: events.append("shared"),
        raising=False,
    )
    monkeypatch.setattr(
        run_batch,
        "detect_harness",
        lambda task_id: events.append("detect") or HarnessKind.PI,
        raising=False,
    )
    monkeypatch.setattr(run_batch, "run_warmup", lambda *args: events.append("warmup"))

    def argv_background(task_id, argv, log_path, cwd=None, env=None):
        launched.update(
            task_id=task_id,
            argv=list(argv),
            log_path=Path(log_path),
            cwd=cwd,
            env=dict(env or {}),
        )
        Path(log_path).write_text('{"type":"stdout-event"}\n', encoding="utf-8")
        events.append("agent")
        return agent

    monkeypatch.setattr(
        run_batch, "run_background_argv", argv_background, raising=False
    )

    def docker_run(command, **kwargs):
        docker_commands.append(list(command))
        if command[:2] == ["docker", "cp"] and str(command[-1]).endswith(
            ":/tmp_workspace/.pi/agent/models.json"
        ):
            copied_config.update(
                json.loads(Path(command[2]).read_text(encoding="utf-8"))
            )
        if command[:2] == [
            "docker",
            "cp",
        ] and ":/tmp_workspace/.pi/agent/sessions/." in str(command[2]):
            destination = Path(command[3])
            destination.mkdir(parents=True, exist_ok=True)
            prompt = launched["argv"][-1]
            session = (
                json.dumps({"type": "session", "cwd": "/tmp_workspace"})
                + "\n"
                + json.dumps(
                    {
                        "type": "message",
                        "id": "user",
                        "message": {"role": "user", "content": prompt},
                    }
                )
                + "\n"
                + json.dumps(
                    {
                        "type": "message",
                        "id": "assistant",
                        "message": {
                            "role": "assistant",
                            "usage": {
                                "input": 11,
                                "output": 5,
                                "totalTokens": 16,
                                "cost": {"total": 0.25},
                            },
                        },
                    }
                )
                + "\n"
            ).encode()
            (destination / "persisted.jsonl").write_bytes(session)
        return completed()

    monkeypatch.setattr(run_batch.subprocess, "run", docker_run)
    monkeypatch.setattr(
        run_batch, "collect_task_output", lambda *args: events.append("collect")
    )
    monkeypatch.setattr(
        run_batch,
        "remove_pi_runtime_before_grading",
        lambda *args: events.append("remove_pi_runtime"),
    )

    def grade(*args):
        events.append("grade")
        grading_transcripts.append(args[5])
        args[4]["scores"] = {"overall_score": 0.75}
        return args[4]

    monkeypatch.setattr(run_batch, "grade_the_task", grade)
    monkeypatch.setattr(
        run_batch, "close_proc_log", lambda proc: events.append("close")
    )
    monkeypatch.setattr(
        run_batch, "remove_container", lambda task_id: events.append("remove")
    )

    result = run_batch.run_single_task(requested_task, "vllm/org/model", thinking="low")
    output_dir = run_dir(output_root)

    assert events[:4] == ["start", "shared", "detect", "warmup"]
    assert launched["argv"][0] == "/usr/local/bin/pi_launcher.py"
    assert launched["cwd"] == "/tmp_workspace"
    assert launched["env"] == {
        "PI_CODING_AGENT_DIR": "/tmp_workspace/.pi/agent",
        "PI_CODING_AGENT_SESSION_DIR": "/tmp_workspace/.pi/agent/sessions",
    }
    assert launched["argv"].count(launched["argv"][-1]) == 1
    assert "Create the requested result." in launched["argv"][-1]
    assert "WILDCLAW_RUN_MARKER" in launched["argv"][-1]
    assert not any(
        "/bin/bash" in command or "pi-super-secret" in " ".join(command)
        for command in docker_commands
    )
    assert copied_config["providers"]["vllm"]["apiKey"] == "pi-super-secret"
    assert copied_config["providers"]["vllm"]["models"][0]["id"] == "org/model"
    assert agent.wait_calls[0] == 17
    assert any(
        str(command[-1]).endswith(":/tmp_workspace/.pi/agent/skills")
        for command in docker_commands
        if command[:2] == ["docker", "cp"]
    )
    assert agent.killed
    assert (
        events.index("collect")
        < events.index("remove_pi_runtime")
        < events.index("grade")
    )
    assert grading_transcripts == [output_dir / "chat.jsonl"]
    assert events[-2:] == ["close", "remove"]
    assert output_dir.joinpath("gateway.log").read_bytes() == b""
    assert b'"type": "session"' in output_dir.joinpath("chat.jsonl").read_bytes()
    assert b"stdout-event" not in output_dir.joinpath("chat.jsonl").read_bytes()
    usage = json.loads(output_dir.joinpath("usage.json").read_text(encoding="utf-8"))
    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 5
    assert usage["request_count"] == 1
    assert usage["elapsed_time"] == 7
    assert result["error"] is None
    assert result["scores"] == {"overall_score": 0.75}
    execution_error = json.loads(
        output_dir.joinpath("task_output/harness/error.json").read_text(
            encoding="utf-8"
        )
    )
    assert execution_error["stage"] == "agent_execution"
    assert "timed out" in execution_error["message"]


def test_pi_rejects_unsupported_options_before_launch_with_placeholders(
    tmp_path, monkeypatch
):
    output_root = tmp_path / "output"
    launched = []
    monkeypatch.setattr(run_batch, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(run_batch, "start_container", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        run_batch, "setup_shared_workspace", lambda task_id: None, raising=False
    )
    monkeypatch.setattr(
        run_batch, "detect_harness", lambda task_id: HarnessKind.PI, raising=False
    )
    monkeypatch.setattr(
        run_batch,
        "run_background_argv",
        lambda *args, **kwargs: launched.append(args),
        raising=False,
    )
    monkeypatch.setattr(
        run_batch,
        "grade_the_task",
        lambda *args: (_ for _ in ()).throw(AssertionError("grading must not run")),
    )
    monkeypatch.setattr(run_batch, "collect_task_output", lambda *args: None)
    monkeypatch.setattr(run_batch, "remove_container", lambda task_id: None)

    result = run_batch.run_single_task(
        task(tmp_path),
        "openrouter/model",
        lobster={"name": "demo", "workspace": "/lobster", "env": ["TOKEN"]},
        models_config={"providers": {}},
    )
    output_dir = run_dir(output_root)

    assert not launched
    assert result["error"] == (
        "unsupported Pi options: --lobster-env, --lobster-name, "
        "--lobster-workspace, --model, --models-config"
    )
    assert json.loads(output_dir.joinpath("score.json").read_text()) == {
        "error": "grading_not_run"
    }
    assert json.loads(output_dir.joinpath("usage.json").read_text()) == empty_usage()
    assert output_dir.joinpath("agent.log").read_bytes() == b""
    assert output_dir.joinpath("gateway.log").read_bytes() == b""
    assert output_dir.joinpath("chat.jsonl").read_bytes() == b""
    error = json.loads(
        output_dir.joinpath("task_output/harness/error.json").read_text()
    )
    assert error["stage"] == "configuration"
    assert error["details"]["unsupported_options"] == [
        "--lobster-env",
        "--lobster-name",
        "--lobster-workspace",
        "--model",
        "--models-config",
    ]


def test_container_setup_failure_preserves_deterministic_placeholders(
    tmp_path, monkeypatch
):
    output_root = tmp_path / "output"
    removed = []
    monkeypatch.setattr(run_batch, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(
        run_batch,
        "start_container",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("startup failed")),
    )
    monkeypatch.setattr(
        run_batch,
        "grade_the_task",
        lambda *args: (_ for _ in ()).throw(AssertionError("grading must not run")),
    )
    monkeypatch.setattr(
        run_batch, "remove_container", lambda task_id: removed.append(task_id)
    )

    result = run_batch.run_single_task(task(tmp_path), "vllm/model")
    output_dir = run_dir(output_root)

    assert result["error"] == "startup failed"
    assert removed == []
    assert json.loads(output_dir.joinpath("score.json").read_text()) == {
        "error": "grading_not_run"
    }
    assert json.loads(output_dir.joinpath("usage.json").read_text()) == empty_usage()
    assert output_dir.joinpath("agent.log").read_bytes() == b""
    assert output_dir.joinpath("gateway.log").read_bytes() == b""
    assert output_dir.joinpath("chat.jsonl").read_bytes() == b""
    assert json.loads(
        output_dir.joinpath("task_output/harness/error.json").read_text()
    ) == {
        "stage": "container_startup",
        "message": "startup failed",
    }


def test_nonzero_agent_exit_records_diagnostic_and_still_grades(tmp_path, monkeypatch):
    events = []
    agent = FakeProcess(returncode=23)
    output_root = tmp_path / "output"
    monkeypatch.setattr(run_batch, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(run_batch, "OPENROUTER_API_KEY", "")
    monkeypatch.setattr(run_batch, "ZHIZENGZENG_API_KEY", "")
    monkeypatch.setattr(run_batch, "SERPER_API_KEY", "")
    monkeypatch.setattr(run_batch.time, "sleep", lambda _: None)
    monkeypatch.setattr(run_batch, "start_container", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        run_batch,
        "setup_shared_workspace",
        lambda task_id: events.append("shared"),
        raising=False,
    )
    monkeypatch.setattr(
        run_batch,
        "detect_harness",
        lambda task_id: HarnessKind.OPENCLAW,
        raising=False,
    )
    monkeypatch.setattr(
        run_batch, "setup_openclaw_workspace", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(run_batch, "setup_skills", lambda *args: None)
    monkeypatch.setattr(run_batch, "run_warmup", lambda *args: None)
    monkeypatch.setattr(run_batch, "set_model", lambda *args: None)

    def background(task_id, bash_cmd, log_path):
        if "gateway" in bash_cmd:
            return FakeProcess()
        return agent

    monkeypatch.setattr(run_batch, "run_background", background)
    monkeypatch.setattr(run_batch, "cal_cost", lambda *args, **kwargs: args[2])
    monkeypatch.setattr(
        run_batch, "collect_task_output", lambda *args: events.append("collect")
    )
    monkeypatch.setattr(
        run_batch,
        "grade_the_task",
        lambda *args: events.append("grade") or args[4],
    )
    monkeypatch.setattr(run_batch, "close_proc_log", lambda *args: None)
    monkeypatch.setattr(
        run_batch, "remove_container", lambda *args: events.append("remove")
    )

    result = run_batch.run_single_task(task(tmp_path), "openrouter/model")
    output_dir = run_dir(output_root)

    assert result["error"] is None
    assert events.index("collect") < events.index("grade")
    error = json.loads(
        output_dir.joinpath("task_output/harness/error.json").read_text()
    )
    assert error == {"stage": "agent_execution", "message": "agent exited with code 23"}
    assert events[-1] == "remove"


def test_harness_detection_failure_preserves_placeholders_and_skips_grading(
    tmp_path, monkeypatch
):
    events = []
    output_root = tmp_path / "output"
    monkeypatch.setattr(run_batch, "OUTPUT_DIR", output_root)
    monkeypatch.setattr(
        run_batch, "start_container", lambda *args, **kwargs: events.append("start")
    )
    monkeypatch.setattr(
        run_batch,
        "setup_shared_workspace",
        lambda task_id: events.append("shared"),
        raising=False,
    )
    monkeypatch.setattr(
        run_batch,
        "detect_harness",
        lambda task_id: (_ for _ in ()).throw(RuntimeError("probe unavailable")),
        raising=False,
    )
    monkeypatch.setattr(
        run_batch,
        "grade_the_task",
        lambda *args: (_ for _ in ()).throw(AssertionError("grading must not run")),
    )
    monkeypatch.setattr(
        run_batch, "remove_container", lambda *args: events.append("remove")
    )

    result = run_batch.run_single_task(task(tmp_path), "vllm/model")
    output_dir = run_dir(output_root)

    assert result["error"] == "probe unavailable"
    assert events == ["start", "shared", "remove"]
    assert json.loads(output_dir.joinpath("score.json").read_text()) == {
        "error": "grading_not_run"
    }
    error = json.loads(
        output_dir.joinpath("task_output/harness/error.json").read_text()
    )
    assert error == {"stage": "harness_detection", "message": "probe unavailable"}
