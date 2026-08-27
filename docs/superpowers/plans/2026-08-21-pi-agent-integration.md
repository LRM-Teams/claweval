# Pi Agent Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Pi as a capability-detected benchmark harness while preserving the existing OpenClaw command and result flow.

**Architecture:** Add small pure modules for harness detection, Pi session parsing, and run artifacts first. Then route the existing task lifecycle through a harness adapter boundary, keeping Docker, grading, summaries, and task parsing shared. Pi uses run-local configuration/session state and the existing vLLM environment variables.

**Tech Stack:** Python 3, Docker CLI, pytest, Pi 0.84.2 JSONL sessions.

---

## Chunk 1: Pure contracts and artifact behavior

- [ ] Add `utils/harness.py` with capability detection, lifecycle dataclasses, adapter protocol, and shared price override.
- [ ] Add `utils/run_artifacts.py` with deterministic placeholders and atomic JSON/file replacement.
- [ ] Add failing tests in `test/test_harness_detection.py` and `test/test_run_artifacts.py` first, then implement and run them.

## Chunk 2: Pi session and configuration

- [ ] Add `utils/pi_session.py` for isolated candidate validation, deterministic selection, and usage accounting.
- [ ] Add `utils/pi_harness.py` for Pi options, run-local models configuration, skills, command construction, and session collection.
- [ ] Add focused tests for malformed/truncated JSONL, duplicate IDs, retained-tail avoidance, options, and secret-safe configuration.

## Chunk 3: Lifecycle integration and OpenClaw regression

- [ ] Add `utils/openclaw_harness.py` as a compatibility wrapper around the current OpenClaw operations.
- [ ] Update `eval/run_batch.py` and `utils/docker_utils.py` incrementally, preserving existing dirty-tree behavior and summaries.
- [ ] Add lifecycle tests for grading eligibility, timeout finalization, placeholders, and cleanup.

## Chunk 4: Image and verification

- [ ] Add the pinned Pi image build files and launcher after probing the installed Pi image.
- [ ] Update `ConstructPiDocker.md`, `HowItWorks.md`, and `.env_example`.
- [ ] Run focused tests, full tests, image smoke checks, and one OpenClaw/Pi end-to-end task where credentials are available.
