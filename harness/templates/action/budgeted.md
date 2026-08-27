## Action Budget

- **Budgeted actions are scarce — spend them like money.** If the task caps a
  specific action (e.g. "at most N searches"), treat efficiency as part of
  correctness.
- Plan all queries before issuing any, and lead with the single most
  discriminative one.
- Stop the moment the answer is verified. Never burn remaining budget
  re-confirming what you already know.
- Prefer direct fetches of authoritative pages you can name (official docs,
  known repositories) over spending another capped query.
- Persistence applies to *retries of failing calls*, not to inflating the count
  of budgeted actions.
