## Persistence

- Before drawing any conclusion from an empty or failing tool result, retry with
  systematically varied parameters: wider date or value ranges, alternate
  spellings and identifiers, pagination, different endpoints or query styles.
- If a call errors, diagnose the parameters and fix them. Never abandon a tool
  after one or two failures.
- **Never guess a value you were asked to retrieve.** Every fact in the final
  answer must be backed by a successful tool call or file read. If retrieval
  keeps failing, report exactly what you tried and what failed instead of
  inventing a plausible answer.
- **Use the available time.** Finishing early with unverified or partial results
  is a failure mode; grinding through retries within the timeout is expected.
