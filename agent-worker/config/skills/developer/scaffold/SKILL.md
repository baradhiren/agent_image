---
name: scaffold
description: Use when starting a new module or feature - stands up a spec-first skeleton (spec stub, BDD scenarios, source + test files) before implementation.
---

# Scaffold a new module (spec-first)

When asked to start a new module or feature:

1. Write or locate its spec in `specs/` (Markdown narrative + flat YAML for
   nested config + BDD `Scenario/Given/When/Then`).
2. Propose the folder structure and tech stack; get sign-off before generating.
3. Create the source file(s) and a matching test file with the BDD scenarios as
   failing tests.
4. Implement the minimal code to make the first scenario pass, then iterate.

Use `search_code` and `get_symbol` to reuse existing patterns instead of
re-inventing them.
