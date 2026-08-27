# JIT harness on ClawEval: first measured run

> Date: 2026-08-27
> Spec: [`docs/jit-harness-spec.md`](./jit-harness-spec.md) (P4.1 + P4.2)
> Suite: ClawEval (external, `agentevals` repo), Pi backend
> Model: `deepseek-v4-flash-0731` / provider `zhizengzeng`, thinking=high
> Driver: [`eval/claweval_jit_bridge.py`](../eval/claweval_jit_bridge.py)
> Raw data: [`results/jit-claweval-20260827/`](./results/jit-claweval-20260827)

## What was measured

Three ClawEval tasks that already had per-task evolution archives, so the empty
appendix baseline and the evolved champion were both known before this run. One
formal trial per arm, seed `tool_grind` (`notes / checklist / persistent`).

ClawEval is appendix-only. The F module has no carrier there and the suite
excludes `write`/`bash`/`edit`, so the bridge downgrades `capability: route` to
`task_only`: the routing fragment would otherwise point the agent at a skill
registry and a shell that do not exist. M/P/A are the only modules exercised.

## Results

| Task | empty appendix | JIT 3072 ch | JIT 1174 ch | evolved champion |
|------|---------------:|------------:|------------:|-----------------:|
| T014_meeting_notes | 0.7028 | 0.7136 | 0.7136 | 1.0 |
| T026_ambiguous_contact_email | 0.0 | 0.0 | 0.0 | 1.0 |
| T118_customer_followup | trial failed | 0.284 | **0.776** | 0.804 |

All six trials reported `status: succeeded`; none of the numbers above is an
infrastructure failure, except the T118 baseline recorded in the archive.

## Findings

**Appendix length dominated content on T118.** The same protocol text, said in
1174 characters instead of 3072, moved `completion` from 0.105 to 0.72 and the
task score from 0.284 to 0.776 — within 0.03 of a champion that had 12 rounds of
task-specific tuning. Nothing about the instructions changed. This is why the
compiler now enforces a 1500-character budget rather than relying on the
overlay's 4096-byte ceiling, and why the budget test enumerates all 192 module
combinations: the worst combination was 1572 characters and none of the six
named seeds happened to exercise that path.

**A generic harness cannot reach a task-specific one.** T026 scored 0.0 on every
dimension in both arms. That task grades whether the agent stops and asks when a
recipient is ambiguous, and its champion wins by naming the recipient and the
rule directly in the appendix. The template library contains no task-specific
content by design (risk table item 1), so there is no generic protocol that
conveys the same information. This is the ceiling of the approach, not a defect
in it.

**T014 was insensitive to the appendix.** Identical scores to four decimal
places across both arms, with `completion` stuck at 0.642. Whatever it loses
points on is not reachable from harness prose.

**The `communication` dimension is 0.0 everywhere**, including champions that
score 1.0 overall, so it does not enter the weighted total. Worth confirming
against the ClawEval grader before any of these numbers are quoted as a
per-dimension result.

## Bearing on the spec

§1 positions JIT as supplying a good starting point for the evolution loop rather
than a finished harness, and these three tasks are consistent with that: the
compiled seed matches or beats the empty-appendix start everywhere, and gets
close to a tuned champion once, but never beats one. What the spec did not
anticipate is how small the improvement is when the appendix is oversized — the
gain on T014 (+0.011) is within noise, and the gain on T118 only appears after
the trim.
