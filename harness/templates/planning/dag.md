## Dependency Planning

- Before acting, write down the subtasks and the dependencies between them: for
  each subtask, what must be known before it can start.
- Identify the subtasks with no unmet dependency. Do those first, and when
  several are independent, batch their tool calls together rather than
  interleaving them one at a time.
- Do not start a dependent subtask before its inputs are confirmed; a guessed
  input silently poisons everything downstream of it.
- Revise the graph when a result invalidates an assumption, and say what
  changed.
