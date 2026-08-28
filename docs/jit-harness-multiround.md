# JIT Harness 多轮演化协议

本协议把“一次生成一个 harness”扩展成有界的三轮闭环：

```text
task prompt + C_τ
       ↓
Round 1: initial harness generation → Pi evaluation
       ↓
reflection: current spec + sanitized feedback
       ↓
Round 2: new candidate → fresh Pi evaluation
       ↓
reflection: current champion + accumulated feedback
       ↓
Round 3: new candidate → fresh Pi evaluation → champion
```

## 反馈边界

反思器可以看到：

- 题目的 `Prompt`（不含 `Automated Checks`、`Expected Behavior` 和 grader
  代码）；
- 当前候选的 `harness.spec.json`；
- `C_τ` 能力注册表；
- 现有 `evolution_feedback.build_feedback()` 产生的结果：overall score、
  soft metrics、耗时、超时和基础设施错误阶段；
- 最近一次 candidate 保留下来的有限 `agent.log` 尾部，作为行为轨迹。

反思器不能看到：

- ground truth、检查代码、grader rationale；
- 正确答案；
- 任务输出目录或 `gt` 文件；
- grader 的内部输出。

这和现有演化器的反馈边界一致：反馈是“评测结果和行为轨迹”，而不是
“标准答案”。trace 只作为 agent 的尝试记录，不能被反思器当成 grader
结论或正确答案。

## Candidate 与回退

每轮都生成新的 `cNNNN/overlay/`，不能原地修改正在使用的 candidate。候选
编译、schema、overlay 校验全部通过后才启动 Pi。每轮结束后：

1. 记录 sanitized feedback；
2. 按 reward 选择 champion；
3. 只有新候选严格更好时，才作为下一轮的当前 spec；
4. 反思失败、spec 非法或编译失败时复用上一版 champion；
5. 第一轮失败时退回具名 `react` seed。

Pi 容器和 agent session 在轮次之间重新初始化。Pi 内核没有热更新；可更新
的是下一轮注入的 appendix、settings 和 skills。

## CLI

```bash
python eval/evolve_task.py \
  --task tasks/04_Search_Retrieval/04_Search_Retrieval_task_4_efficient_search.md \
  --model vllm/gpt-5.5 \
  --editor-model vllm/gpt-5.5 \
  --jit-rounds 3 \
  --output-root evolved
```

`--jit-rounds 3` 表示三个生成 harness 的正式评测，不包含额外 baseline。
编辑器使用 `JIT_EDITOR_API_KEY` / `OPENAI_API_KEY`，endpoint 使用
`JIT_EDITOR_BASE_URL`，也可以通过 CLI 显式传入。

三轮是演化上限，不是统计重复次数。要估计 harness 的运行方差，应使用
`avg_k` 或独立重复实验；不要把三轮中的不同候选当作同一候选的三次
rollout 均值。
