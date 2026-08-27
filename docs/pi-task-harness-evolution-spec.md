# WildClawBench × Pi：Per-Task Harness 自进化 Spec

> Status: draft for implementation  
> Date: 2026-08-26  
> Scope: Pi harness only（`DOCKER_IMAGE=pi-agent-wildclawbench-*`）  
> Non-goal: 改模型权重；跨题共享「万能 harness」；把进化器变成直接刷 `solution` 产物

---

## 1. 目标

当前 WildClawBench 评测里的 Pi agent 是**静态 harness**：同一套 skills / settings / system 附录跑所有题。

本方案要变成：

1. **冻模型**，只进化 **harness**（prompt 附录、skills、Pi settings 等）。
2. **一题一进化**：题与题几乎无关，不为跨题迁移优化。
3. **跑题时加载该题 champion harness**。
4. **对内迭代优先**：反馈默认 `soft-metrics`（可读子项分数），便于更快抠分；同时保留 `reward_only` 档位供日后公平消融。

参考：

| 来源 | 采用什么 | 不采用什么 |
|------|----------|------------|
| [AHE / `feature/reward-only-feedback`](https://github.com/LRM-Teams/self-harness/tree/feature/reward-only-feedback) | harness 进化外环、`change_manifest`、反馈隔离、sanitize bundle | NexAU 全套组件模型可后置；不必一次搬完 |
| [DarwinX](https://arxiv.org/abs/2608.07545) | 冻模型、archive/谱系、加法编辑、avg@k 抗噪 | 跨题 preserve-and-extend / 跨题 merge（题间无关时性价比低） |
| [`experiment/pi-evolution`](https://github.com/LRM-Teams/self-harness/tree/experiment/pi-evolution) | 可选：单题内 elite/diverse/adaptive 多路搜索、budget、可恢复 | 默认进化 `solution.py` 候选 |

**一句话：** AHE 反馈与 harness 进化为主，约束改成 per-task；DarwinX 借谱系理念；pi-evo 作单题内可选加强。

---

## 2. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│  evolve_loop (host)                                         │
│    for task_id in selected_tasks:                           │
│      archive = evolved/<task_id>/                           │
│      while budget_left:                                     │
│        propose harness child (Pi evolve agent)              │
│        evaluate via WildClawEvaluationAdapter               │
│          → run_batch --task ... --evolved-harness <child>   │
│        write soft-metrics feedback + sanitized trace        │
│        update graph / memory / champion                     │
└───────────────────────────┬─────────────────────────────────┘
                            │ mounts champion or child
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  WildClaw run_batch (existing)                              │
│    docker: pi-agent-wildclawbench                           │
│    inject: base Pi + task skills + **task harness overlay** │
│    grade → score.json                                       │
│    pack feedback (soft-metrics | reward_only)               │
└─────────────────────────────────────────────────────────────┘
```

两阶段用法：

| 模式 | 命令意图 | harness |
|------|----------|---------|
| `eval` | 正式/对比评测 | `baseline` 或已冻结的 `champion` |
| `evolve` | 对内抠分 | 多轮改 harness，吃 feedback bundle |

---

## 3. 进化对象（Candidate = Harness Snapshot）

每个 candidate 是一份**可挂载目录**，不是解题产物。

### 3.1 目录约定

```text
evolved/
  <task_id>/
    meta.json                 # task_id, model, feedback_mode, created_at
    graph.json                # candidate 谱系：parent / reference / operator / score
    memory.md                 # Retrospective Memory（跨代短记，仅本 task）
    baseline/                 # 只读快照：最初静态 harness
    candidates/
      <cid>/
        manifest.json         # 见 3.2
        overlay/              # 实际注入容器的内容
          SYSTEM_APPENDIX.md  # 可选：拼到 task prompt 前/后的短附录
          settings.json       # 可选：Pi agent settings 片段（白名单字段）
          skills/             # 可选：额外或覆盖的 skill bundles
        eval/
          run_id/
            score.json
            usage.json
            feedback.json     # 给 evolve 看的包（已按 mode 过滤）
            agent.log         # 或 sanitized 截断版
        change_manifest.json  # 相对 parent 的编辑说明（AHE 风格）
    champion -> candidates/<cid>
```

### 3.2 `manifest.json`（最小字段）

```json
{
  "candidate_id": "c0007",
  "task_id": "03_Social_Interaction_task_1_meeting_negotiation",
  "parent_ids": ["c0003"],
  "reference_ids": ["c0001"],
  "operator": "refine|repair|crossover|restart",
  "generation": 3,
  "model": "vllm/gpt-5.5",
  "feedback_mode": "soft-metrics",
  "status": "reserved|evaluated|champion|rejected",
  "overall_score": 0.895,
  "metrics": {},
  "created_at": "ISO-8601"
}
```

### 3.3 允许进化的表面（v1 收窄）

**v1 允许：**

- `overlay/SYSTEM_APPENDIX.md`（建议 ≤ 2–4KB）
- `overlay/skills/*/SKILL.md` 与 skill 内脚本（新增或覆盖；禁止覆盖评测 gt）
- `overlay/settings.json` 白名单字段（如 thinking 默认值、工具开关；实现时列死列表）

**v1 禁止：**

- 改 `tasks/*.md` 的 Prompt / Automated Checks / gt
- 改 Docker 镜像、launcher、grading 代码
- 改 host `.env` 密钥
- Safety 类任务默认关闭进化，或仅允许「更稳拒答」方向（配置黑名单）

---

## 4. 反馈协议

### 4.1 默认：`soft-metrics`（对内抠分）

Evolve agent **可以看：**

- `overall_score`
- `score.json` 全部 **metric → float**
- agent 自有轨迹（`agent.log` / pi session，可截断）
- 过程元数据：`timed_out`、`elapsed_sec`、`exit_code`、token/usage 摘要

Evolve agent **不可以看：**

- `## Expected Behavior` / `## Grading Criteria` 原文
- `## Automated Checks` 源码
- `workspace/**/gt/`
- grading 长 traceback 中夹带的答案片段（打包时剥离）

`feedback.json` 示例：

```json
{
  "feedback_mode": "soft-metrics",
  "task_id": "03_Social_Interaction_task_1_meeting_negotiation",
  "overall_score": 0.895,
  "metrics": {
    "read_init": 1.0,
    "contradiction_detected": 0.3,
    "boss_notified": 1.0
  },
  "elapsed_sec": 201.4,
  "timed_out": false,
  "trace_ref": "eval/<run_id>/agent.log"
}
```

### 4.2 可选：`reward_only`（公平消融）

仅暴露：

- 标量 `overall_score`（或 `pass = overall_score >= threshold`）
- sanitized agent trace
- 白名单过程元数据（布尔/数值）

不暴露任何子项名与子项分。配置切换，不改主循环。

### 4.3 Sanitize 规则

打包 feedback 时：

1. 只从 `output/.../score.json`、`usage.json`、`agent.log` 取数。
2. 不把 `tasks/<task>.md` 的判题段写入 evolve 工作区。
3. evolve 工具的可读根目录白名单：`evolved/<task_id>/`、当前 feedback bundle、（可选）task **Prompt 段**单独抽出的 `problem_prompt.md`。
4. 显式拒绝路径：`**/gt/**`、`**/*grade*`、task md 全文（若需 prompt，用 parser 只抽 `## Prompt`）。

---

## 5. 进化循环（Per-Task）

### 5.1 外环（AHE 风格）

对每个 `task_id`：

```text
1. seed baseline harness → evaluate → feedback_0
2. while evaluation_budget > 0:
     a. 选 parent（及可选 reference）
     b. evolve agent 产出 child overlay + change_manifest
     c. validate overlay（结构/大小/白名单）
     d. evaluate(child) 消耗 1 个 budget
     e. 写 graph 边、更新 memory
     f. 若 overall_score 创新高 → 更新 champion
3. 冻结 champion；记录 final report
```

### 5.2 Parent 选择（v1 简单版）

- **elite**：当前最高 `overall_score` 的可行 candidate  
- **repair**：最近一次低分且有明显低分子项的 candidate  
- **restart**：从 baseline 重新开一条（停滞时）

v1.1 可选接入 pi-candidate-evolution 的 3-lane（elite / diverse / adaptive）与 crossover；**candidate 语义必须是 harness 目录**。

### 5.3 `change_manifest`（强制）

每次提交至少包含：

```json
{
  "changes": [
    {
      "id": "chg-1",
      "description": "短描述改了什么",
      "files": ["overlay/SYSTEM_APPENDIX.md"],
      "target_metrics": ["contradiction_detected"],
      "predicted_effect": "提高矛盾邮件识别",
      "risk": "可能增加拒答/变慢"
    }
  ]
}
```

下一代用 metric 升降做弱证伪；连续无效编辑触发 repair/restart。

### 5.4 Budget 与成本

- 配置项：`evaluation_budget`（每题正式评测次数，含 baseline）
- 建议起步：单题 `8–16`；并行 `evaluation_workers=1`（先稳）
- 每个 child **恰好占用 1 次** WildClaw 正式 evaluate（与 pi-evo「formal slot」一致）
- 可选 `avg_k`（同 harness 跑 k 次取均值）抗噪；v1 默认 `k=1`，重要晋级再用 `k=2/3`

### 5.5 Champion 晋级

- 主规则：`overall_score` 严格更高则晋升  
- 并列：更短 elapsed / 更少 tokens 优先  
- Soft-metrics 下可加：目标低分项提升且 overall 不降（可选）

---

## 6. 与现有 `run_batch` 的改动点

### 6.1 新增 CLI

```bash
# 评测：加载某题已进化 harness
python eval/run_batch.py \
  --task tasks/03_Social_Interaction/03_Social_Interaction_task_1_meeting_negotiation.md \
  --model vllm/gpt-5.5 \
  --evolved-harness evolved/03_Social_Interaction_task_1_meeting_negotiation/champion

# 进化入口（新）
python eval/evolve_task.py \
  --task tasks/03_Social_Interaction/03_Social_Interaction_task_1_meeting_negotiation.md \
  --model vllm/gpt-5.5 \
  --feedback-mode soft-metrics \
  --evaluation-budget 12 \
  --output-root evolved
```

### 6.2 容器注入（Pi 路径）

在现有 `prepare_pi_run` / `copy_pi_skill` 之后增加 `apply_harness_overlay(task_id, overlay_dir)`：

1. 拷贝 `overlay/skills/` → Pi skills 目录（后写覆盖同名）  
2. merge `overlay/settings.json`（白名单 deep-merge）  
3. 若存在 `SYSTEM_APPENDIX.md`，拼进发给 Pi 的 message（与 task Prompt 分离存储）

Baseline = 当前静态行为（无 overlay）。

### 6.3 EvaluationAdapter

```text
WildClawEvaluationAdapter
  validate(candidate_dir) -> {valid, feedback}
  evaluate(candidate_dir) -> {score=overall_score, feasible, metrics, feedback.json}
```

内部调用现有 `run_single_task(...)`，禁止复制一套 Docker 逻辑。

### 6.4 反馈打包

新增 `utils/evolution_feedback.py`：

- `build_feedback(output_dir, mode=soft-metrics|reward_only) -> feedback.json`
- `stage_evolve_workspace(...)`：只挂白名单文件给 evolve agent

---

## 7. Evolve Agent（谁来改 harness）

v1 可用第二种 Pi 会话（或 host 侧 LLM + 文件工具），角色固定为 **harness editor**：

输入：

- `problem_prompt.md`（仅 Prompt）
- 当前 parent `overlay/`
- `feedback.json` + trace
- `memory.md`
- 本 spec 的可写表面约束

输出：

- 新的 `overlay/`
- `change_manifest.json`

系统约束（写进 evolve prompt）：

- 只改 overlay 白名单  
- 根据低分 metrics + 轨迹提改动  
- 禁止寻找 gt / Checks  
- 改动尽量小、可证伪  

实现上可先复用仓库内 Pi 调用方式；后续再嵌 self-harness 的 evolve_agent 资产。

---

## 8. 配置

`configs/evolve/example.yaml`（新建）：

```yaml
model: vllm/gpt-5.5
feedback_mode: soft-metrics   # or reward_only
evaluation_budget: 12
avg_k: 1
output_root: evolved

overlay:
  max_appendix_bytes: 4096
  max_skill_files: 32
  settings_allowlist: ["thinkingDefault"]

task_blacklist:
  - 06_Safety_Alignment_task_6_prompt_injection
  - 06_Safety_Alignment_task_7_skill_injection
  - 06_Safety_Alignment_task_10_malicious_skill

search:
  strategy: simple           # simple | pi_lanes
  operators: [refine, repair, restart]
```

---

## 9. 成功标准

### 9.1 功能性

- [ ] 无 overlay 时分数与当前静态基线一致  
- [ ] `--evolved-harness` 能稳定注入并复现 champion 分数（同模型、同种子条件下接近）  
- [ ] `soft-metrics` feedback **不含** Checks/gt/Expected Behavior  
- [ ] budget 用尽后有可审计 `graph.json` + 每 candidate 的 score  
- [ ] 中断后可从 `graph.json` resume  

### 9.2 效果（对内）

先以已跑通题为目标：

- Task: `03_Social_Interaction_task_1_meeting_negotiation`  
- Baseline ≈ `0.90`（已知 `contradiction_detected≈0.3`）  
- 目标：同模型下 overall 提升，或该低分项明显提高且 overall 不降  

### 9.3 报告口径

对内 soft-metrics 结果需标注：

> Evolved under soft-metrics feedback (per-metric scores visible). Not claimed as strict reward-only.

若发外部数字，至少附 `baseline` vs `champion`，并说明 feedback 档位。

---

## 10. 实施分期

### P0 — 可加载 harness（0.5–1d）

1. overlay 目录约定 + `apply_harness_overlay`  
2. `run_batch --evolved-harness`  
3. 手工改一个 appendix 验证注入生效  

### P1 — 反馈与单步进化（1–2d）

1. `build_feedback(soft-metrics|reward_only)`  
2. `evolve_task.py`：baseline eval → 人工/LLM 一改 → 再 eval → 更新 champion  
3. `graph.json` / `change_manifest` 落盘  

### P2 — 自动循环（2–3d）

1. budget 循环 + refine/repair/restart  
2. sanitize 工作区与路径白名单  
3. resume  
4. 单题打满 8–12 budget  

### P3 — 可选加强

1. 单题内 pi-lanes（elite/diverse/adaptive）  
2. avg@k 晋级  
3. 批量 `--tasks-file` 排队进化（仍 per-task 独立 archive）  

---

## 11. 明确不做

- 不把 grader 源码或 gt 喂给 evolve（即使 soft-metrics）  
- 不做跨题 harness merge（除非未来单开实验）  
- 不进化「任务最终产物」冒充 harness 进化  
- 不对 Safety 对抗题默认开启无约束进化  
- 不在文档/对外口径把 soft-metrics 说成 reward-only  

---

## 12. 关键文件（实现时）

| 路径 | 作用 |
|------|------|
| `docs/pi-task-harness-evolution-spec.md` | 本 spec |
| `eval/evolve_task.py` | 进化入口 |
| `utils/evolution_feedback.py` | 反馈打包 |
| `utils/harness_overlay.py` | overlay 校验与注入 |
| `configs/evolve/example.yaml` | 默认配置 |
| `evolved/` | 运行产物（gitignore） |
| `eval/run_batch.py` | 增加 `--evolved-harness` |
| `utils/docker_utils.py` / Pi 注入路径 | 调用 overlay apply |

---

## 13. 决策摘要

| 问题 | 决定 |
|------|------|
| 跟哪条上游？ | 主跟 AHE/`reward-only-feedback`；题间策略 per-task；pi-evo 可选 |
| 进化什么？ | Pi harness overlay（附录 / skills / 白名单 settings） |
| 反馈默认？ | **soft-metrics**（对内抠分） |
| 细项文案 / Checks / gt？ | **永不给 evolve** |
| 子项分数？ | soft-metrics **给**；reward_only **不给** |
| 跨题合并？ | v1 **不做** |
| 何时加载？ | `run_batch --evolved-harness evolved/<task_id>/champion` |
