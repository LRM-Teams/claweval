## Environment Ground Rules

**Never trust the real-world clock.** The environment's "today" may differ from
the actual date; establish it from initialization files, timestamps on existing
data, or service responses. If a date-based query returns empty, sweep adjacent
days and wider ranges before concluding no data exists.

## Working Notes

Keep a `notes.md` for the whole run. Each time you settle a subgoal, append one
line: what you established, and the call or file that proves it. Record outcomes
only. Before answering, check every claim traces to an entry.

## Checklist Discipline

Before your first substantive tool call, enumerate every required subtask and
every service or file the task mentions, and keep that checklist. Mark each item
verified or explicitly exhausted. Answer only once nothing is unresolved.

## Persistence

Before concluding anything from an empty or failing result, retry with
systematically varied parameters: wider ranges, alternate identifiers,
pagination. Never abandon a tool after one or two failures. **Never guess a
value you were asked to retrieve.** Use the available time; finishing early with
unverified results is a failure.
