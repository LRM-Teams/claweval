import unittest
from unittest.mock import patch

from utils import docker_utils


class StartContainerPrintTest(unittest.TestCase):
    def test_start_container_prints_image_and_command(self):
        completed = type(
            "Completed", (), {"returncode": 0, "stdout": "abc123\n", "stderr": ""}
        )()

        with (
            patch.object(docker_utils, "DOCKER_IMAGE", "demo:image"),
            patch.object(docker_utils, "SERPER_API_KEY", "serper-secret"),
            patch.object(docker_utils, "JINA_KEY", "jina-secret"),
            patch(
                "utils.docker_utils.subprocess.run", return_value=completed
            ) as mock_run,
            patch("utils.docker_utils.os.path.exists", return_value=False),
            patch("builtins.print") as mock_print,
        ):
            docker_utils.start_container("task-1", "/workspace")

        cmd = mock_run.call_args.args[0]
        mock_print.assert_any_call("[task-1] Docker image: demo:image")
        mock_print.assert_any_call(f"[task-1] Docker command: {' '.join(cmd)}")
        assert ["-e", "SERPER_API_KEY"] == cmd[
            cmd.index("SERPER_API_KEY") - 1 : cmd.index("SERPER_API_KEY") + 1
        ]
        assert ["-e", "JINA_KEY"] == cmd[
            cmd.index("JINA_KEY") - 1 : cmd.index("JINA_KEY") + 1
        ]
        assert "serper-secret" not in cmd
        assert "jina-secret" not in cmd

    def test_start_container_rejects_missing_required_static_env(self):
        with (
            patch.object(docker_utils, "SERPER_API_KEY", ""),
            patch.object(docker_utils, "JINA_KEY", "jina-secret"),
            patch("utils.docker_utils.subprocess.run") as mock_run,
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Missing required environment variables: SERPER_API_KEY"
            ):
                docker_utils.start_container("task-1", "/workspace")

        mock_run.assert_not_called()

    def test_prepare_pi_run_creates_workspace_local_directories(self):
        completed = type("Completed", (), {"returncode": 0})()

        with patch(
            "utils.docker_utils.subprocess.run", return_value=completed
        ) as mock_run:
            docker_utils.prepare_pi_run("task-1")

        assert mock_run.call_args.args[0] == [
            "docker",
            "exec",
            "task-1",
            "mkdir",
            "-p",
            "/tmp_workspace/.pi/agent/skills",
            "/tmp_workspace/.pi/agent/sessions",
            "/tmp_workspace/.pi/control",
        ]

    def test_copy_pi_sessions_reads_workspace_local_session_directory(self):
        completed = type("Completed", (), {"returncode": 0})()

        with (
            patch(
                "utils.docker_utils.subprocess.run", return_value=completed
            ) as mock_run,
            patch("utils.docker_utils.Path.mkdir"),
        ):
            assert docker_utils.copy_pi_sessions("task-1", "/host/sessions")

        assert mock_run.call_args.args[0] == [
            "docker",
            "cp",
            "task-1:/tmp_workspace/.pi/agent/sessions/.",
            "/host/sessions",
        ]

    def test_remove_pi_runtime_before_grading_removes_only_runtime_directory(self):
        completed = type(
            "Completed", (), {"returncode": 0, "stdout": "", "stderr": ""}
        )()

        with patch(
            "utils.docker_utils.subprocess.run", return_value=completed
        ) as mock_run:
            docker_utils.remove_pi_runtime_before_grading("task-1")

        command = mock_run.call_args.args[0]
        assert command[:5] == ["docker", "exec", "task-1", "python3", "-c"]
        script = command[5]
        assert "Path('/tmp_workspace/.pi')" in script
        assert "shutil.rmtree(source)" in script


if __name__ == "__main__":
    unittest.main()
