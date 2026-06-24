---
name: code-check
description: Use when auditing a diff - a structured security + logic review pass over changed code, grounded in the memory graph rather than a re-read of the repo.
---

# Code check (security + logic review)

Review the current diff, not the whole repo. For each changed symbol:

1. **Impact:** call `impact_of(symbol)` — list callers that could break.
2. **Spec alignment:** call `spec_for(symbol)` — confirm the change matches its
   spec; flag drift.
3. **Security:** check input validation, injection surfaces, secret handling,
   and over-broad permissions.
4. **Logic:** check edge cases, error paths, and off-by-one / null handling.

Report findings as concrete, located issues. Do not propose unrelated cleanups.
