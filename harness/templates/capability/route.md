## Tool Selection Order

- Before reaching for a general-purpose tool, check whether an available skill
  covers the step. A skill that matches the step is always preferred over
  improvising the same work by hand.
- Read the skill's own instructions before invoking it, and follow them as
  written rather than approximating them.
- Escalate in this order: a skill built for the exact step, a general skill for
  the domain, then raw shell or HTTP as the last resort.
- If a skill fails, diagnose its inputs and retry it before falling back to the
  next level; do not silently drop to raw calls after one failure.
