# WildClawBench × Pi：JIT Harness 生成 Spec

> Status: draft for implementation
> Date: 2026-08-27
> 前置 spec: [`docs/pi-task-harness-evolution-spec.md`](./pi-task-harness-evolution-spec.md)（P0/P1 已落地）
> 参考: [Scaling Harness Intelligence via Just-in-Time Harness Evolution](https://arxiv.org/pdf/2608.25593)（下称 JIT-Agent）
> Scope: Pi harness only（`DOCKER_IMAGE=pi-agent-wildclawbench-*`）
> Non-goal: 训练 harness 生成模型；改 Pi 运行时内核；改 grading

---

## 1. 为什么要加这一层

现有 `eval/evolve_task.py` 的形态是：baseline 跑一次 → 改一个 overlay → 再跑一次 → 按 `overall_score` 选 champion。用 JIT-Agent 的分类法，这属于 Table 1 的 **AOT (test-time editing)**：harness 是被「搜」出来的，成本 `≈ 单次评测 × budget`，而且 `evolved/<task_id>/` 之间完全隔离，第 40 道题不会从前 39 道题里学到任何东西。

JIT-Agent 的做法是把搜索**摊销进生成**：给定

```
c_τ = (任务 τ, 协议 Π, 能力注册表 C_τ, 检索到的参考 harness E_τ)
```

一次生成出该题的 harness，静态校验通过就直接执行，失败走有界修复。成本 `≈ 1 次生成 + 1 次评测`。

对 WildClawBench 的 50+ 道题，这是能不能全量铺开的区别。本 spec 定义把这套东西接到现有 overlay 机制上的最小改动。

**两者不是替代关系。** JIT 负责给每道题一个**好的起点**（当前起点是「空 overlay」），已有的进化循环负责在起点之上继续抠分。落地后 `evolve_task.py` 的 baseline 可以从「静态 harness」换成「JIT 生成的 harness」。

---

## 2. 四模块协议映射到 Pi

论文把 harness 分解为 `h = (M, P, A, F)`，运行时依赖序 `M → P → F → A`：

| 模块 | 论文语义 | Pi 上的实现载体 |
|------|----------|------------------|
| **M** memory | 历史 → 视图 | `SYSTEM_APPENDIX.md` 里的记录/压缩协议 + 约定的 scratch 文件 |
| **P** planning | 视图 → 局部指令 | appendix 里的 todo / DAG / 分解协议 + 约定的 plan 文件 |
| **A** action | 控制推进与动作发射 | appendix 里的循环纪律（自检、重试、终止条件）+ `settings.json` 的 `thinkingDefault` |
| **F** capability | 工具/技能编排 | `overlay/skills/**`（挂哪些 skill、以什么顺序用） |

### 2.1 关键约束：Pi 内核不可改

Pi 是固定的 ReAct 运行时，我们改不了 `A` 的真正内核（不能换成真正的递归调度器或真正的上下文压缩器）。所以：

- **F 是真正可写的**——挂载 skill 就是改能力注册表，这是硬的。
- **M / P / A 只能做到「协议级实例化」**——用 appendix 规定 agent 必须维护什么文件、按什么节奏复核、什么时候停。这是软约束，靠模型遵循度生效。

这一点必须在文档和对外口径里写清楚，不能声称实现了论文的完整设计空间。已有的 `configs/evolve/seeds/tool_grind/SYSTEM_APPENDIX.md` 其实就是一个手写的 `(M_full, P_checklist, A_persistent, F_all)` 实例——第 4 条「enumerate every required subtask and keep a checklist」是 P，第 2/3 条「retry with varied parameters, never guess」是 A。本 spec 做的事就是把这种手写产物**结构化、可生成、可检索**。

### 2.2 M/P/A/F 的取值枚举（v1）

生成器只能在这个封闭集合里选，不能自由发挥。这是把「程序合成」降级成「类型化装配」的关键。

**M（memory）**

| 取值 | 含义 | 渲染 |
|------|------|------|
| `full` | 不做上下文管理（Pi 默认） | 不渲染任何片段 |
| `notes` | 强制维护 `notes.md`，每完成一个子目标追加一条结论 | 记录协议片段 |
| `resum` | 长任务：定期把已确认事实压成摘要块，后续只引用摘要 | 压缩协议片段 |
| `evidence` | 深搜类：每条事实必须带来源（URL / 文件路径 / 工具调用） | 证据表协议片段 |

**P（planning）**

| 取值 | 含义 | 渲染 |
|------|------|------|
| `none` | 无显式规划（论文的 `P_∅`） | 不渲染 |
| `checklist` | 开工前枚举全部子任务，逐项勾掉 | 清单协议片段 |
| `dag` | 显式依赖图，可并行的分支先并行 | DAG 协议片段 |
| `decomp` | 递归分解，子问题独立求解后汇总 | 分解协议片段 |

**A（action）**

| 取值 | 含义 | 渲染 |
|------|------|------|
| `react` | Pi 默认循环 | 不渲染 |
| `persistent` | 失败即诊断参数并重试，禁止一两次失败就放弃 | 重试纪律片段 |
| `verify` | 每个产物落盘后必须回读校验 | 自检片段 |
| `budgeted` | 有动作预算的题：先规划全部查询，验证到即停 | 预算纪律片段 |

**F（capability）**

| 取值 | 含义 | 渲染 |
|------|------|------|
| `task_only` | 只用题目自带 skill（Pi 默认） | 不挂载额外 skill |
| `plus:<names>` | 额外挂载具名 skill | `overlay/skills/<name>/` |
| `route` | 挂载 skill 并在 appendix 里规定选用顺序 | skill + 路由片段 |

组合空间 `4×4×4×3 ≈ 192`，足够表达差异，又小到可以穷举验证。

---

## 3. `harness.spec.json`

Overlay 目录新增一个声明式文件，作为 candidate 的**规格**（overlay 其余内容由它编译产生，不手写）：

```json
{
  "spec_version": 1,
  "task_id": "04_Search_Retrieval_task_4_efficient_search",
  "task_type": "search_budgeted",
  "modules": {
    "memory": "evidence",
    "planning": "checklist",
    "action": "budgeted",
    "capability": {"mode": "task_only", "skills": []}
  },
  "settings": {"thinkingDefault": "high"},
  "rationale": "题目限定最多 N 次搜索，预算纪律优先于持久重试；答案需可溯源。",
  "reference_ids": ["04_Search_Retrieval_task_3_constraint_search:c0003"],
  "generated_by": "vllm/gpt-5.5",
  "created_at": "2026-08-27T06:00:00+00:00"
}
```

**`task_type`** 是检索键，取值来自固定枚举：`search_budgeted` / `search_deep` / `code_repo` / `code_puzzle` / `chat_extract` / `creative_media` / `productivity_crawl`。由生成器给出，落进 bank 索引。

**`rationale`** 不进容器，只用于 bank 里给后续生成器当参考上下文（论文 `E_τ` 的一部分）。

### 3.1 与 `validate_overlay` 的关系

`utils/harness_overlay.py` 的 `ALLOWED_TOP_LEVEL` 增加 `harness.spec.json`；若该文件存在，额外做 schema 校验（字段齐全、枚举值合法、引用的 skill 在注册表里存在）。**这就是论文 `Valid_Π` 的静态部分。**

现有限制原样保留：appendix ≤ 4KB、skills ≤ 32 文件、settings 白名单、`gt`/`grade` 路径禁止。编译器产出的 appendix 必须在预算内，超了就报错而不是截断。

---

## 4. 模板库（mini HarnessFactory）

论文的 `B_0` 有 13 个 seed。我们缩成 Pi 上可行的片段库，放 `harness/templates/`：

```text
harness/templates/
  memory/{notes,resum,evidence}.md
  planning/{checklist,dag,decomp}.md
  action/{persistent,verify,budgeted}.md
  capability/route.md
  preamble.md            # 所有 harness 共用的开场
```

每个 `.md` 是一段 appendix 片段（200–600 字），`full` / `none` / `react` / `task_only` 没有对应文件（渲染为空）。

同时给出 6 个**具名种子组合**，对应论文 Table 2 的可映射子集，作为生成器的 few-shot 参考和 bank 的冷启动内容：

| 种子名 | M | P | A | F | 对应论文 |
|--------|---|---|---|---|----------|
| `react` | full | none | react | task_only | ReAct |
| `plan_execute` | full | checklist | react | task_only | Plan-and-Execute |
| `resum` | resum | none | persistent | task_only | ReSum |
| `flash_dag` | full | dag | react | task_only | Flash-Searcher |
| `evidence_search` | evidence | checklist | budgeted | task_only | GAM 的检索侧简化 |
| `tool_grind` | notes | checklist | persistent | route | 现有手写 seed |

`tool_grind` 直接由现有 `configs/evolve/seeds/tool_grind/SYSTEM_APPENDIX.md` 拆片段回填，保证新体系至少不比现状差。

---

## 5. 编译器

`utils/harness_compile.py`

```python
def compile_spec(spec: dict, templates_root: Path, dest: Path) -> dict:
    """spec → overlay/{SYSTEM_APPENDIX.md, settings.json, skills/**}

    返回渲染审计记录（用了哪些片段、appendix 字节数）。
    纯函数式：同一 spec 必须编译出逐字节一致的 overlay。
    """
```

渲染顺序固定为 `preamble → M → P → F → A`（与论文运行时依赖序一致，读起来也是「先说记什么、再说怎么规划、再说有什么工具、最后说怎么执行」）。

确定性是硬要求：bank 里只存 spec，不存 overlay；任何时候都能从 spec 重建出完全一样的 overlay。

---

## 6. 能力注册表 `C_τ`

生成器必须知道这道题有什么可用能力，否则 `capability` 只能瞎选。构造来源：

1. `parse_task_md` 已给出的 `skills` 字段（题目自带 skill）
2. `skills/` 目录下的通用 bundle（`agent-browser`、`video-frames`、`self-improving-agent-3.0.5`）——读每个 `SKILL.md` 的首段描述作为能力摘要
3. 题目的 `env` 字段（哪些 API key 可用，决定某些 skill 是否真的能跑）

产物是一个 JSON 清单喂给生成器。**注册表里不存在的 skill 名字必须在 schema 校验阶段被拒绝**，这是最常见的一类生成错误。

---

## 7. Bank 与检索 `E_τ`

```text
evolved/_bank/
  index.json          # 全部条目
  specs/<entry_id>.json
```

条目：

```json
{
  "entry_id": "04_Search_Retrieval_task_3_constraint_search:c0003",
  "task_id": "04_Search_Retrieval_task_3_constraint_search",
  "task_type": "search_budgeted",
  "category": "04_Search_Retrieval",
  "modules": {"memory": "evidence", "planning": "checklist", "action": "budgeted", "capability": {"mode": "task_only", "skills": []}},
  "reward": 0.87,
  "latency_sec": 412.0,
  "cost_usd": 0.21,
  "model": "vllm/gpt-5.5",
  "rationale": "…"
}
```

**检索**：新题按 `task_type` 精确匹配取 reward 最高的 3 条；不足则按 `category` 补；再不足用具名种子补齐。这就是 `E_τ`。

**回填**：`evolve_task.py` 每次 `record_evaluation` 后追加一条。现有 `evolved/*/graph.json` 里已评估的节点可以一次性导入（它们没有 spec，标 `modules: null`，只作 reward 参照，不作 few-shot）。

`reward` 取 `overall_score`，`latency_sec` 取 `feedback.elapsed_sec`，`cost_usd` 取 `usage.cost_usd`——三个字段现有 `evolution_feedback.py` 都已经在采集了。

---

## 8. 验证与有界修复

论文 Stage II 的推理侧对应物。修复上限 2 轮（论文 `K* ≤ 2`）。

| 层 | 检查 | 诊断回灌内容 |
|----|------|--------------|
| L1 schema | 字段/枚举/skill 存在性 | 具体哪个字段非法、合法取值是什么 |
| L2 compile | 模板齐全、appendix 字节数 | 超限多少字节 |
| L3 overlay | 现有 `validate_overlay` | 原有报错 |
| L4 smoke | 容器起得来、overlay 注入成功、agent 出第一条动作 | `task_output/harness/error.json` 的 `stage` |

L1–L3 是**离线的**（不烧 API、不起容器），失败重生成极便宜。L4 才需要容器，且失败时直接退回具名种子 `react` 而不是继续修——保证任何情况下都有可执行 harness，不会因为生成器抽风导致这道题跑不出分。

---

## 9. 选择准则

现有 `update_champion` 用 `(overall_score, -elapsed_sec)`。换成论文 Eq.12 的偏序：

```
h⁺ ≻ h⁻  ⟺  r⁺ > r⁻ ∧ ℓ⁺ ≤ ℓ⁻ ∧ κ⁺ ≤ κ⁻ ∧ (ℓ⁺ < ℓ⁻ ∨ κ⁺ < κ⁻)
```

严格用这个偏序会导致大量不可比对（分高但更贵的进不来），所以实际晋升规则放宽为：**reward 为主通道，latency / cost 只在 `r_i ≥ b_r` 时作为 tiebreak 激活**（论文 Eq.20 的 `I[r_i ≥ b_r]` 门控）。这样既不会让「分持平但贵一倍」的 harness 上位，也不会卡死正常的分数提升。

`avg_k ≥ 2` 时三个量都取多次 rollout 均值。

---

## 10. 接线改动

| 文件 | 动作 |
|------|------|
| `harness/templates/**` | 新建片段库与具名种子 |
| `utils/harness_compile.py` | 新建：spec → overlay |
| `utils/harness_spec.py` | 新建：schema 定义与校验 |
| `utils/harness_registry.py` | 新建：构造 `C_τ` |
| `utils/harness_bank.py` | 新建：bank 读写与检索 |
| `utils/harness_jit.py` | 新建：生成器（含 ≤2 轮修复） |
| `utils/harness_overlay.py` | `ALLOWED_TOP_LEVEL` 加 `harness.spec.json` + schema 校验 |
| `eval/run_batch.py` | 新增 `--jit-harness`：跑题前生成 overlay 再走现有注入路径 |
| `eval/evolve_task.py` | baseline 可选用 JIT spec；`update_champion` 换偏序；评估后回填 bank |
| `configs/evolve/example.yaml` | 增加 `jit:` 段（生成模型、修复轮数、检索条数） |

`run_batch.py` 侧改动很小：现有 `--evolved-harness` 已经在 429–438 行做「先 `validate_overlay`、再 `load_system_appendix`、容器起来后 `apply_harness_overlay`」。`--jit-harness` 只是在这之前插一步「生成到临时目录」，之后完全复用同一条路径。

---

## 11. 分期与验收

| Phase | 交付 | 退出标准 |
|-------|------|----------|
| **P4.1** | spec schema + 模板库 + 编译器 | 6 个具名种子都能编译出合法 overlay；`tool_grind` 编译结果与现有手写文件语义等价 |
| **P4.2** | `C_τ` 构造 + `--jit-harness`（先固定用种子，不接生成器） | meeting 题用 `--jit-harness=tool_grind` 跑通，分数不低于现有手写 overlay |
| **P4.3** | 生成器 + 有界修复 | 全 50 题离线生成，L1–L3 通过率 ≥ 95%，人工抽查 10 题 rationale 合理 |
| **P4.4** | bank + 检索 + 回填 | 导入现有 `evolved/` 数据；新题能检索到 3 条同类参考 |
| **P4.5** | 解耦选择准则 | `update_champion` 换偏序，在已有 archive 上回放不改变已知正确的晋升结果 |

P4.1 和 P4.2 完全离线/单题，可以先做；P4.3 之后才需要大规模跑。

---

## 12. 风险

| 风险 | 对策 |
|------|------|
| **安全题被 JIT 绕过**——生成器为提分自己写「忽略可疑指令」类 appendix，使 `06_Safety_Alignment` 失去意义 | `task_blacklist` 在 JIT 入口同样强制拒绝；模板库里不提供任何涉及指令信任判断的片段 |
| appendix 膨胀吃掉上下文 | 4KB 硬限制在编译期报错；片段库每段限长 |
| 生成器挂载不存在的 skill | `C_τ` 白名单在 L1 校验；这是预期中最高频的错误类型 |
| 「JIT 更好」是自我实现的预言——用 soft-metrics 调出来的 spec 再拿 soft-metrics 评 | 对外报数必须同时给出 `feedback_mode`；正式对比用 `reward_only` 重跑 |
| bank 冷启动时检索到的参考质量差 | 前期用具名种子兜底；bank 条目数 < 5 时不注入 `E_τ` |
| 论文的 Stage I–III 是训练流程，我们只做推理侧 | 文档和对外口径明确写「prompt 级复现，未训练生成器」，不声称复现论文结果 |

---

## 13. 与前置 spec 的关系

| 前置 spec 章节 | 本 spec 的变化 |
|----------------|----------------|
| §3.1 candidate 目录 | overlay 内增 `harness.spec.json`；新增 `evolved/_bank/` |
| §3.3 允许进化的表面 | 不变（仍是 appendix / settings / skills），但改为由 spec 编译产生 |
| §4 feedback | 不变，`elapsed_sec` / `cost_usd` 复用为 bank 的 latency / cost |
| §5 进化循环 | baseline 起点从「空 overlay」改为「JIT spec」 |
| §6 run_batch | 增 `--jit-harness`，复用 `--evolved-harness` 的注入路径 |
