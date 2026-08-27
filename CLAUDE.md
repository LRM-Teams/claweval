# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

WildClawBench is an end-to-end evaluation harness for AI agents. The host orchestrates per-task Docker containers running the OpenClaw agent, drives the agent over a task prompt, then grades the artifacts the agent leaves behind. It is **not** a library — the runtime is the benchmark.

## Common Commands

Setup (one-time):

```bash
pip install -r requirements.txt
bash script/prepare.sh          # downloads YouTube videos, extracts dot_git.tar.gz, fetches sam3.pt
docker load -i Images/wildclawbench-ubuntu_v1.2.tar
```

Running evaluations:

```bash
# Single task
python eval/run_batch.py --task tasks/01_Productivity_Flow/01_Productivity_Flow_task_1_arxiv_digest.md

# Whole category (sequential)
python eval/run_batch.py --category 04_Search_Retrieval

# Whole category in parallel containers
python eval/run_batch.py --category 04_Search_Retrieval --parallel 4 --model openrouter/openai/gpt-5.4

# All 60 tasks
python eval/run_batch.py --category all --parallel 4 --model openrouter/anthropic/claude-opus-4.6

# Custom (non-OpenRouter) model endpoint — JSON gets injected into ~/.openclaw/openclaw.json["models"]
python eval/run_batch.py --category 01_Productivity_Flow --models-config my_api.json --model my-openai-proxy/my-model

# Personal "lobster" workspace evaluation (custom SOUL.md, skills, memory)
python eval/run_batch.py --category all --parallel 4 --model openrouter/xx/xxx \
    --lobster-name your-lobster --lobster-workspace /path/to/workspace
```

Cleanup of stranded containers (after Ctrl+C etc.):

```bash
docker ps -a --filter "ancestor=wildclawbench-ubuntu:v1.2" -q | xargs -r docker rm -f
```

There are no unit tests, linters, or build steps — `script/run.sh` is just a scratchpad of example invocations. `test_docker_utils.py` at the repo root is an ad-hoc script, not part of a test suite.

## Architecture

### Execution flow (`eval/run_batch.py` → `utils/docker_utils.py`)

For each task, `run_single_task` performs this sequence:

1. `parse_task_md` reads YAML frontmatter + Markdown sections from `tasks/<category>/*.md` (see `utils/task_parser.py`). Sections used: `Prompt`, `Workspace Path`, `Skills`, `Env`, `Warmup`, `Automated Checks`. The parser splits on level-2 `##` headings — **prompts must not contain `##`** or they get truncated.
2. `start_container` boots a fresh `wildclawbench-ubuntu:v1.2` container named after the task, mounts `<workspace>/exec` to `/app:ro`, copies `<workspace>/tmp` to `/tmp_workspace/tmp`, and injects env vars listed in the task's `Env` section (values pulled from host `.env`).
3. `setup_workspace` copies `/app/.` → `/tmp_workspace` (read-write) and symlinks `/root/.openclaw/workspace` → `/tmp_workspace`. The agent always works inside `/tmp_workspace` — never `/app`.
4. `setup_skills` copies skill directories from `skills/` into `/root/skills/` based on the task's `Skills` list.
5. `inject_openclaw_models` (if `--models-config`) overwrites `openclaw.json["models"]`. `${MY_PROXY_API_KEY}` placeholders are expanded from the host env.
6. `run_warmup` runs the task's bash warmup commands inside the container.
7. The OpenRouter API key is injected into `/root/.openclaw/agents/main/agent/auth-profiles.json`, the `imageModel.primary` config is set to the same model, the `openclaw gateway` is started in the background on `GATEWAY_PORT` (default 18789), and finally `openclaw agent --session-id chat --message <prompt>` runs with the task's `timeout_seconds`.
8. Grading: `utils/grading.py` extracts the `grade(transcript, workspace_path)` function from the task's `Automated Checks` code block, ships it into the container as `/tmp/_grade_runner.py`, and executes it with `cwd=/tmp_workspace`. The function must return a dict of `metric_name -> float in [0, 1]` and should include an `overall_score` key. The host's `<workspace>/gt/` ground-truth dir is copied in as `/tmp_workspace/gt` only after the agent finishes — this avoids data leakage during the run.
9. Artifacts (`/tmp_workspace/results/.` and `/tmp/openclaw/.`) are pulled back to `output/<category>/<task_id>/<short_model>_<ts>_<runid>/task_output/`. Token usage is parsed from `chat.jsonl`. The container is then removed.

Parallelism is just `ThreadPoolExecutor` over `run_single_task`; each task gets its own container, so concurrency is bounded only by `--parallel` and Docker resources.

### Task layout

- `tasks/<NN>_<Category>/<NN>_<Category>_task_<N>_<short>.md` — the canonical task definition (parsed by `parse_task_md`). The filename pattern matches the glob `*task_*.md`.
- `workspace/<NN>_<Category>/task_<N>_<short>/` — per-task data:
  - `exec/` — mounted read-only into the container at `/app`, copied to `/tmp_workspace/`. The agent sees this as its working directory.
  - `tmp/` — copied to `/tmp_workspace/tmp/`.
  - `gt/` — ground truth, copied in **only** at grading time as `/tmp_workspace/gt/`.
- `tasks/task0_template.md` is the annotated template for new tasks.
- `skills/` — OpenClaw skill bundles (e.g. `agent-browser`, `video-frames`). Tasks list which skills to inject by name.

### Output layout

```
output/<category>/<task_id>/<short_model>_<timestamp>_<runid>/
├── score.json       # metric -> float, includes overall_score
├── usage.json       # tokens, cost, elapsed_time
├── agent.log        # stdout/stderr of `openclaw agent`
├── gateway.log      # stdout/stderr of `openclaw gateway`
├── chat.jsonl       # full conversation trace from /root/.openclaw/agents/main/sessions/
└── task_output/     # files the agent produced (results/ + /tmp/openclaw/.)
```

`runid` is a 6-char random hex so parallel/repeat runs of the same task don't collide. Per-category and global summaries are written to `output/<category>/summary_<model>.json` and `output/summary_all_<model>.json`.

### Configuration via .env

`.env_example` documents the keys. The host loads `.env` via `python-dotenv` in both `eval/run_batch.py` and `utils/docker_utils.py`. Notable ones:

- `DOCKER_IMAGE` (default `wildclawbench-ubuntu:v0.4` in code, but README/env_example use `v1.2`).
- `OPENROUTER_API_KEY`, `BRAVE_API_KEY` — Brave is required for Search & Retrieval tasks.
- `DEFAULT_MODEL`, `DEFAULT_PARALLEL`, `GATEWAY_PORT`, `TASKS_SUBDIR`, `OUTPUT_SUBDIR`, `TMP_WORKSPACE`.
- Any extra key listed in a task's `Env` section must exist in `.env` (or the host environment) to be forwarded into the container.

### Adding a new task

Copy `tasks/task0_template.md`, place it under `tasks/<category>/<category>_task_<N>_<short>.md`, create a matching `workspace/<category>/task_<N>_<short>/` directory with `exec/` and (if needed) `gt/`, and write a `grade(**kwargs)` function in the `Automated Checks` block that returns `{"overall_score": float, ...}`. The grader runs inside the container with `cwd=/tmp_workspace` — do not assume host paths.

## Notes

- The 06_Safety_Alignment category contains adversarial *defense* tests (prompt injection bait, leaked-credential decoys, malicious skill injection). The repo ships these as inputs to evaluate whether agents resist them; they are not attack tooling.
- Agent prompts are prefixed with a short host-controlled system prompt in `run_single_task` before being sent to `openclaw agent`.
- The `--thinking` flag flows through to `openclaw config set agents.defaults.thinkingDefault`.
