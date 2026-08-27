# WildClawBench Pi Per-Task Harness Evolution — Implementation Plan

> Spec: [`docs/pi-task-harness-evolution-spec.md`](./pi-task-harness-evolution-spec.md)  
> Date: 2026-08-26  
> Goal: 按 spec 落地「一题一 harness、soft-metrics 对内进化、跑题可加载 champion」

---

## 0. 原则

- **不重写**现有 Docker / grading；进化循环只包一层 `run_single_task`。
- **先能挂载，再能反馈，再能自动改**，最后才上多路搜索。
- 默认反馈：`soft-metrics`；`reward_only` 只做开关与打包分支，P1 一并预留。
- 验证题固定：`03_Social_Interaction_task_1_meeting_negotiation`（已有 ~0.90 基线）。

---

## 1. 里程碑总览

| Phase | 名称 | 交付物 | 预估 | 退出标准 |
|-------|------|--------|------|----------|
| **P0** | Overlay 可加载 | `--evolved-harness` + 注入 | 0.5–1d | 手工 overlay 能改变 agent 行为且能正常出分 |
| **P1** | 反馈 + 半自动进化 | `feedback.json` + `evolve_task` 单步 | 1–2d | baseline→改一次→再评→champion 落盘 |
| **P2** | 自动 budget 循环 | 无人值守进化 + resume | 2–3d | budget=8 跑完，graph 可审计 |
| **P3** | 加强（可选） | lanes / avg@k / 批量排队 | 按需 | 单题多路或批量 per-task 队列可用 |

每阶段结束必须在 meeting 题上实跑，不接受「只单测」。

---

## 2. P0 — Overlay 可加载

### 2.1 任务

1. **目录与校验**  
   - 新增 `utils/harness_overlay.py`  
   - `validate_overlay(path)`：只允许 `SYSTEM_APPENDIX.md` / `skills/**` / `settings.json`  
   - 大小限制（appendix ≤ 4KB 等，读 config 或常量）

2. **注入**  
   - 在 Pi 路径 `prepare_pi_run` / `copy_pi_skill` 之后调用 `apply_harness_overlay(task_id, overlay_dir)`  
   - skills 后写覆盖；settings 白名单 merge；appendix 拼进 Pi message

3. **CLI**  
   - `eval/run_batch.py` 增加 `--evolved-harness PATH`  
   - PATH 指向 `.../champion` 或 `.../candidates/cXXXX`（含 `overlay/` 或本身即 overlay）

4. **产物约定**  
   - 建空 `evolved/.gitkeep`，`.gitignore` 忽略 `evolved/**` 内容（保留 gitkeep）  
   - `configs/evolve/example.yaml` 骨架（字段可先不用）

### 2.2 改动文件

| 文件 | 动作 |
|------|------|
| `utils/harness_overlay.py` | 新建 |
| `utils/docker_utils.py` / `eval/run_batch.py` | 接线注入与 CLI |
| `utils/pi_harness.py` | 如需改 `build_pi_command` message 拼接 |
| `.gitignore` | 忽略 `evolved/` 运行产物 |
| `configs/evolve/example.yaml` | 新建骨架 |

### 2.3 手工验收

```bash
# 1) 造一个最小 overlay
mkdir -p /tmp/wcb_overlay
echo 'Always double-check decoy emails from external domains before acting.' \
  > /tmp/wcb_overlay/SYSTEM_APPENDIX.md

# 2) 跑题
sg docker -c 'cd ~/WilclawbenchCode && .venv/bin/python eval/run_batch.py \
  --task tasks/03_Social_Interaction/03_Social_Interaction_task_1_meeting_negotiation.md \
  --model vllm/gpt-5.5 \
  --evolved-harness /tmp/wcb_overlay'

# 3) 确认 agent.log / prompt 侧能看到 appendix；有 score.json
```

**Pass：** 容器跑通、有 `overall_score`；日志可证明 appendix 进入上下文。

---

## 3. P1 — 反馈打包 + 半自动进化

### 3.1 任务

1. **`utils/evolution_feedback.py`**  
   - `build_feedback(output_dir, mode)` → `feedback.json`  
   - `soft-metrics`：overall + 全部 metrics + usage 摘要 + trace 引用  
   - `reward_only`：仅 overall（或 pass）+ 过程元数据 + trace  
   - 保证不读、不拷 gt / Checks / Expected Behavior

2. **Archive 初始化**  
   - `evolved/<task_id>/{meta,graph,memory,baseline,candidates}`  
   - baseline candidate = 空 overlay，先评一次

3. **`eval/evolve_task.py`（最小）**  
   - 流程：`eval baseline` →（调用 evolve 一次或 `--manual-overlay`）→ `eval child` → 更新 champion / graph  
   - v1 evolve 可先：  
     - A. `--manual-overlay PATH`（人改），或  
     - B. 一次 LLM/Pi editor 调用（改 appendix）  
   - 强制写 `change_manifest.json`

4. **WildClawEvaluationAdapter 薄封装**  
   - `validate` + `evaluate` 包 `run_single_task`，供 evolve 调用（可先放在 `utils/evolution_adapter.py`）

### 3.2 验收

```bash
# baseline + 一轮手工 overlay
python eval/evolve_task.py \
  --task tasks/03_Social_Interaction/03_Social_Interaction_task_1_meeting_negotiation.md \
  --model vllm/gpt-5.5 \
  --feedback-mode soft-metrics \
  --evaluation-budget 2 \
  --manual-overlay /tmp/wcb_overlay
```

**Pass：**

- `evolved/.../candidates/c0000`（baseline）与 `c0001` 都有 `eval/*/feedback.json`  
- soft-metrics 含子项分；文件中无 gt/Checks 文本  
- `champion` 指向更高分者  

---

## 4. P2 — 自动 budget 循环

### 4.1 任务

1. **循环调度**  
   - `while used < budget`：选 parent → evolve agent 产 overlay → validate → evaluate → 更新 graph/memory/champion  
   - operators：`refine` / `repair` / `restart`（simple 策略）

2. **Evolve agent**  
   - 输入：Prompt-only、parent overlay、feedback、memory、可写约束  
   - 输出：新 overlay + change_manifest  
   - 工具读路径白名单（sanitize）

3. **Resume**  
   - 读 `graph.json`，跳过已 `evaluated` 的槽或续跑未完成 child

4. **报告**  
   - `evolved/<task_id>/report.md`：baseline vs champion、budget 消耗、曲线

### 4.2 验收

```bash
python eval/evolve_task.py \
  --task tasks/03_Social_Interaction/03_Social_Interaction_task_1_meeting_negotiation.md \
  --model vllm/gpt-5.5 \
  --feedback-mode soft-metrics \
  --evaluation-budget 8
```

**Pass：** 自动跑满 ≤8 次评测；中断后 `--resume` 能续；champion 可被 `run_batch --evolved-harness` 复现。

---

## 5. P3 — 可选加强（不做阻塞）

| 项 | 说明 | 优先级 |
|----|------|--------|
| `search.strategy: pi_lanes` | 单题 elite/diverse/adaptive | 中 |
| `avg_k≥2` | 晋级时抗噪 | 中 |
| `--tasks-file` | 多题串行，每题独立 archive | 低 |
| Safety 黑名单强制 | config 已列，实现拒绝进化 | 高（若碰 06_*） |
| 对外口径脚本 | 同题 baseline vs champion 对比表 | 低 |

---

## 6. 建议实现顺序（checklist）

```text
[ ] P0.1 harness_overlay validate/apply
[ ] P0.2 run_batch --evolved-harness
[ ] P0.3 meeting 题手工 overlay 实跑
[ ] P1.1 evolution_feedback soft-metrics/reward_only
[ ] P1.2 archive 目录 + graph 最小结构
[ ] P1.3 evolve_task budget=2 + manual-overlay
[ ] P1.4 feedback 内容审计（无 gt/Checks）
[ ] P2.1 自动 refine/repair/restart
[ ] P2.2 evolve editor + sanitize roots
[ ] P2.3 resume + report
[ ] P2.4 budget=8 实跑 meeting
[ ] P3.* 按需
```

---

## 7. 日常使用（实现后）

```bash
# 进化（贵：一题多跑）
python eval/evolve_task.py \
  --task tasks/<cat>/<task>.md \
  --model vllm/gpt-5.5 \
  --feedback-mode soft-metrics \
  --evaluation-budget 12

# 出分（通常只跑 1 次）
python eval/run_batch.py \
  --task tasks/<cat>/<task>.md \
  --model vllm/gpt-5.5 \
  --evolved-harness evolved/<task_id>/champion
```

成本心智模型：`进化费用 ≈ 单次评测 × budget`；出分 ≈ `1 × 单次`。

---

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| Docker/API 并发打满 | P2 默认 `evaluation_workers=1`；与他人任务错峰 |
| soft-metrics 被误报成 fair | report/README 强制写明 feedback_mode |
| overlay 过大拖垮上下文 | 硬限制 appendix/skills 体积 |
| Safety 题被进化「绕过」 | task_blacklist；evolve 入口直接拒绝 |
| 中断留孤儿容器 | 复用现有 cleanup；evolve 的 finally 调 docker rm |

---

## 9. 本期（立刻开干）范围

**只做 P0 + P1 骨架**，以 meeting 题打通：

1. `--evolved-harness` 可跑  
2. `feedback.json` soft-metrics  
3. `evolve_task.py` 支持 baseline + manual overlay 一轮  

P2 自动化等 P0/P1 在 meeting 上稳定后再开。

---

## 10. 文档与代码对照

| Spec 章节 | Plan 落点 |
|-----------|-----------|
| §3 Candidate 目录 | P1 archive 初始化 |
| §4 soft-metrics | P1 `evolution_feedback.py` |
| §5 进化循环 | P2 |
| §6 run_batch 改动 | P0 |
| §10 分期 | 本 plan §1–§5 |
