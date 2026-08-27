import subprocess
from pathlib import Path

from utils import grading


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_run_grading_copies_and_passes_transcript(tmp_path, monkeypatch):
    transcript_path = tmp_path / "chat.jsonl"
    transcript_path.write_text(
        '{"type":"message","message":{"role":"assistant","content":"safe"}}\n',
        encoding="utf-8",
    )
    copied = []
    runner_code = {}

    def run(command, **kwargs):
        if command[:2] == ["docker", "cp"]:
            copied.append(command)
            if str(command[-1]).endswith(":/tmp/_grade_runner.py"):
                runner_code["text"] = Path(command[2]).read_text(encoding="utf-8")
            return completed()
        return completed(stdout='{"overall_score": 1.0}')

    monkeypatch.setattr(grading.subprocess, "run", run)

    scores = grading.run_grading(
        "task-1",
        "def grade(**kwargs): return {'overall_score': len(kwargs['transcript'])}",
        tmp_path / "output",
        transcript_path=transcript_path,
    )

    assert scores == {"overall_score": 1.0}
    assert any(
        command[2] == str(transcript_path)
        and command[3] == "task-1:/tmp/_grade_transcript.jsonl"
        for command in copied
    )
    assert "transcript.append(json.loads(line))" in runner_code["text"]
    assert "grade(transcript=transcript" in runner_code["text"]


def test_run_grading_without_transcript_keeps_empty_fallback(tmp_path, monkeypatch):
    copied_destinations = []

    def run(command, **kwargs):
        if command[:2] == ["docker", "cp"]:
            copied_destinations.append(command[-1])
            return completed()
        return completed(stdout='{"overall_score": 0.0}')

    monkeypatch.setattr(grading.subprocess, "run", run)

    scores = grading.run_grading(
        "task-1",
        "def grade(**kwargs): return {'overall_score': len(kwargs['transcript'])}",
        tmp_path / "output",
    )

    assert scores == {"overall_score": 0.0}
    assert "task-1:/tmp/_grade_transcript.jsonl" not in copied_destinations
