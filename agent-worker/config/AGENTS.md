# Engineering DNA

Cross-tool base instructions for every role in this workspace. These apply
regardless of which agent CLI is running.

## Spec-first, behaviour-driven
- Write or read the spec before generating code: requirements, schemas, API
  contracts, and BDD scenarios (`Scenario / Given / When / Then`).
- Keep `specs/` and code in sync. The spec is the source of truth for the stack.

## Propose before you build
- Propose the folder structure and tech stack **before** writing code, and get
  sign-off. Pin every library version — never depend on a floating range.

## Fix the root cause only
- Reproduce a bug first (failing test or `curl`), keep the test in the repo, fix
  only the root cause. Defer unrelated cleanups/renames to a separate task — no
  drive-by changes.

## Context hygiene — retrieve, don't re-dump
- This workspace has a persistent memory layer. **Retrieve from it; do not
  re-read or re-paste the repo into context.** Use the memory MCP tools:
  - `search_code` — semantic search over code.
  - `search_docs` — semantic search over docs/specs.
  - `get_symbol` — look up a symbol by qualified name.
  - `impact_of` — who calls this symbol (what breaks if I change it).
  - `spec_for` — which spec defines this symbol.
  - `add_knowledge` — link a spec file to a symbol.
- Commit at meaningful checkpoints; commits refresh the memory index.
