# Per-Project Snapshot-able Memory — Design

- **Date:** 2026-06-25
- **Status:** Implemented — see [plan](../plans/2026-06-26-per-project-snapshot-memory.md)
- **Phase:** Q1 — prerequisite for the orchestration layer (Q2–Q4, see appendix)

## Problem

Today the memory database lives in a single global Docker named volume
(`agent_image_pgdata`), inside Docker Desktop's VM. Two consequences:

1. **No per-project isolation.** Point `PROJECT_DIR` at a different repo and the
   stack reuses the *same* memory, bleeding one project's knowledge into another.
2. **Memory does not travel with the source.** There is no way to stash a
   project's memory alongside its code and reload it on resume.

We want memory to be **per-project, co-located with the source, gitignored, and
reloadable on resume**, while never silently losing agent-authored knowledge.

## Key insight

The memory DB has two kinds of data:

- **Derivable** — the structure graph + chunks + embeddings. 100% reproducible
  from source via `reconcile` (~80s for a small project). Disposable.
- **Irreplaceable** — agent-authored **spec links** and `embedding_config`.
  Cannot be rebuilt from source. Note: the `add_knowledge` MCP tool writes into
  the **`spec_links`** table; there is no separate `knowledge` table. "Agent
  knowledge" in this spec means `spec_links` rows.

The on-disk snapshot is therefore a **cache + a knowledge store**. If it is
missing, stale, or incompatible, we rebuild the derivable part with `reconcile`;
the irreplaceable part (`spec_links`) is what we must protect at all costs.

## Decisions (resolved during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Snapshot scope | **Full DB** (graph + embeddings + `spec_links`), then an incremental catch-up `reconcile` on resume. Fast resume, no re-embedding unless files changed. |
| 2 | Lifecycle trigger | **Auto-restore on startup; explicit save.** `dump` is a command the lifecycle helper / orchestrator calls before teardown. No fragile shutdown traps. |
| 3 | Location & ignore | **Self-ignoring `.agent-memory/`** in `PROJECT_DIR`, containing a `.gitignore` with `*`. Never touches the repo's root `.gitignore`. |
| 4 | Unwritable target | **Fail fast + fallback**, never silent loss. Probe writability at startup; on failure persist to a named volume and warn loudly. |

## Architecture & components

### On-disk layout

```
PROJECT_DIR/.agent-memory/
  .gitignore     # contains "*"  -> the whole dir is self-ignoring
  snapshot.dump  # `pg_dump -Fc` of the entire memory database
  meta.json      # compatibility metadata (see below)
```

`pg_dump -Fc` (compressed custom format) is chosen over plain SQL: smaller,
faster `pg_restore`, and it captures the schema, data, and the `vector`
extension dependency. The file is an opaque blob, which is fine because it is
gitignored.

### `meta.json` — the compatibility guard

Written at `dump` time, validated at `restore` time:

```json
{
  "schema_version": 1,
  "pg_major": 16,
  "code_embed": { "model": "BAAI/bge-small-en-v1.5", "dim": 384 },
  "doc_embed":  { "model": "BAAI/bge-small-en-v1.5", "dim": 384 },
  "source_head": "<git HEAD sha at dump time>",
  "created_at": "<iso8601>",
  "location": "co-located | fallback-volume"
}
```

On restore we refuse the snapshot (and fall back to a fresh seed) if the
embedding **model/dim** differs or the Postgres major version differs. This
prevents a silent `embedding_config` mismatch (mixed-dimension vectors).

A **provider-only** change (e.g. `local` ↔ `remote`, same model + dim) is *not*
a mismatch — it produces the same vector space — so it is **not** part of the
compatibility check. Because `Repository.ensure_embedding_config` currently
treats any change (including provider) as fatal, the catch-up reconcile after a
restore would otherwise crash on a provider-only difference. The implementation
must make `ensure_embedding_config` tolerant of a provider-only change (update
the stored provider; keep model/dim changes fatal).

### New module: `memory/snapshot.py`

A thin, well-bounded unit wrapping `pg_dump`/`pg_restore` against `DATABASE_URL`.

Public interface:

- `dump(target_dir: str) -> None` — write `snapshot.dump` + `meta.json` into
  `target_dir`, creating the self-ignoring `.gitignore` if missing. Atomic:
  writes to a temp file and `os.rename`s into place. Raises (non-zero CLI exit)
  on any failure.
- `restore(source_dir: str) -> bool` — validate `meta.json`; if compatible,
  `pg_restore` into a clean DB and return `True`; if missing/incompatible/
  corrupt, return `False` (caller then seeds).
- `meta_is_compatible(meta: dict, settings: Settings) -> bool` — pure function,
  unit-testable without Postgres.
- CLI: `python -m memory.snapshot dump|restore <dir>`.

### Startup init step

A one-shot run when the stack comes up (a `memory.run_worker`-adjacent entry, or
a dedicated `python -m memory.startup /project`). Sequence:

1. **Reset** the live DB to a clean state (`DROP SCHEMA public CASCADE; CREATE
   SCHEMA public` — i.e. drop all memory tables). This is what guarantees
   isolation: the snapshot — not whatever the global volume held — is the source
   of truth on every start.
2. **Probe writability** of `.agent-memory/` (touch + remove a temp file)
   *before* any agent works. If unwritable, switch the snapshot home to a named
   volume `agent-memory` and emit a loud warning (memory will not co-locate).
3. **Restore or seed:**
   - `snapshot.dump` present **and** `meta.json` compatible → `pg_restore`
     (recreates the tables and data from the dump).
   - else → create the tables from `sql/001_schema.sql`, then `reconcile
     /project` to seed from source.
4. **Catch-up:** always finish with an *incremental* `reconcile /project`
   (hash-diff) to pick up source changes since the snapshot; re-embeds only
   changed files.

## Data flow

### Startup (automatic)

```
stack up
  -> wait for db healthy (compose depends_on)
  -> reset DB (DROP SCHEMA public CASCADE)  # isolation
  -> probe .agent-memory writability         # fail fast / fallback
  -> if snapshot compatible: pg_restore
     else: create tables + reconcile (seed)
  -> incremental reconcile (catch up)
```

### Shutdown (explicit)

```
caller (lifecycle helper / orchestrator)
  -> python -m memory.snapshot dump /project/.agent-memory   # atomic, verified
  -> on success: docker compose down
  -> on failure (non-zero): DO NOT down; surface error
```

## Compose / mount changes

- The init runs as a **one-shot `init` service** (same memory image). Because it
  resets the DB on startup, **every DB consumer must wait for it to complete** —
  the ingest `worker`, the standalone `memory` MCP service, **and** the root
  `agent-worker` all gain `depends_on: { init: { condition:
  service_completed_successfully } }`. Otherwise an agent can query the DB mid-
  reset/restore.
- The init/snapshot container mounts `${PROJECT_DIR}` **read-write** so startup
  can create `${PROJECT_DIR}/.agent-memory` next to the source (the subdir does
  not exist yet, so a narrower mount is impractical). The steady-state ingest
  worker keeps its read-only `/project` mount for safety.
- Add a named volume `agent-memory` as the unwritable-fallback snapshot home.
- The live Postgres volume becomes a disposable working store (reset on every
  start); it may remain a named volume for speed.

## Error handling

Guiding rule: derivable data is disposable; agent knowledge is never silently
lost. The stack degrades to working-but-loud, never working-but-lossy.

| Condition | Behavior |
|-----------|----------|
| `.agent-memory` not writable | Fall back to named volume `agent-memory`; **loud warning** that memory is not co-located. No data loss. |
| `dump` cannot write | Atomic temp+rename; exit **non-zero**; caller must not tear down. |
| `snapshot.dump` corrupt / `pg_restore` fails | Log; fall back to fresh seed (derivable only — safe). |
| `meta.json` incompatible (model/dim/pg-major) | Skip restore; seed fresh. |
| No snapshot yet (first run) | Seed via `reconcile`. |

## Testing

- `dump` → `restore` round-trip preserves row counts **including the
  `spec_links`** rows written by `add_knowledge`.
- `meta_is_compatible`: matching vs differing model/dim/pg-major (pure unit).
- Unwritable `.agent-memory` → falls back to the named volume and warns; data
  still persisted (never the old "in-memory only" path).
- Failed `dump` exits non-zero.
- Incompatible/corrupt snapshot → seed path is taken.
- Self-ignoring `.gitignore` is created on first `dump`.
- Startup decision (restore vs seed) selected correctly for present/absent
  snapshot.

## Out of scope (YAGNI / deferred)

- Periodic auto-snapshots mid-session (the orchestrator owns clean teardown;
  revisit only if long crash-prone sessions prove it necessary).
- Concurrent stacks for multiple projects on one host (ports collide; assume one
  active project stack at a time).
- Cross-architecture / cross-pg-major snapshot portability beyond what the
  `meta.json` guard already refuses.

---

## Appendix — Forward context: the orchestration layer (Q2–Q4)

**Status: NOT YET SPECCED.** This appendix captures the agreed *direction* from
brainstorming so a future session has enough understanding to pick it up. It is
**not** an implementation contract — the orchestration layer gets its own
brainstorm → spec → plan cycle. Do not implement from this section.

### Purpose

A universal entrypoint that takes a spec / feature request / bug, spins up the
project's stack, drives role agents to do the work, reviews against the spec, and
winds everything down — reporting what changed.

### Agreed structural decisions

- **Agents are Python programs using the Claude Agent SDK** (not an LLM left to
  drive its own lifecycle). The control flow — gates, retries, teardown — is
  explicit and testable in Python.
- **The user authors each agent's behavior per their domain knowledge.** Role
  agents (orchestrator, developer, reviewer, design) get human-designed
  instructions + role-scoped skills. A dedicated brainstorm will cover *how* the
  user expresses that domain knowledge into each agent.
- **Warm per-role containers.** Keep one long-lived container per role running;
  the orchestrator `docker exec`s a **fresh agent session per task** (each
  session is stateless, so "different session per task" is free — pay startup
  once). One container per role keeps role skills/instructions cleanly isolated.
- **Full permissions inside the sandbox.** Role agents run with
  `--dangerously-skip-permissions`; acceptable because they are contained.
- **Coordination via a `tasks` table** in the same Postgres memory DB:
  `(id, spec_ref, status, assignee_role, worktree/branch, summary,
  review_status, artifacts)`. The orchestrator manages it; agents update their
  row and `add_knowledge` what they did. This single table is both the
  orchestration state and the parallel-coordination bus.

### Entry/exit lifecycle

```
agentctl run <spec|issue>          # name TBD
  -> resolve project via `pwd`, set PROJECT_DIR
  -> docker compose up              # Q1 startup: restore-or-seed memory
  -> orchestrator (Python + Agent SDK):
       read spec -> decompose -> dispatch role agents -> collect reports
       -> review against spec -> iterate until done
  -> python -m memory.snapshot dump # Q1 hook, before teardown
  -> docker compose down            # only after a verified dump
  -> report what changed to the user
```

### Flow: waterfall gate + parallel fan-out

Keep the serial **spec/plan review gate** (high value, spec-driven discipline):

```
User -> Orchestrator -> Reviewer (reviews the PLAN) -> Orchestrator -> approve
```

Then **parallel fan-out** for independent implementation tasks:

```
Orchestrator
  -> decompose approved plan into independent units
  -> N Developer agents in parallel, each in its own git WORKTREE
     (worktrees prevent parallel writes from stomping the shared /workspace)
  -> Reviewer pipelines: review each slice as it lands
  -> converge: integration review
  -> report + teardown
```

This mirrors the superpowers `executing-plans` + `dispatching-parallel-agents`
patterns, applied across containers, coordinated through the `tasks` table and
the shared memory DB.

### Dependency & build order

1. **Q1 — per-project snapshot memory** (this spec). Prerequisite: parallel
   agents + warm containers assume isolated, reloadable per-project memory.
2. Warm per-role containers + `exec`-per-task.
3. Orchestrator (Python + Agent SDK) + `tasks` table coordination.
4. Parallel fan-out with git worktrees.

### Open questions for the orchestration brainstorm

- How the user authors per-agent domain instructions (format, where they live,
  how they compose with the existing AGENTS.md + role overlays).
- Who performs task decomposition (a planning agent vs. the orchestrator) and how
  independence between tasks is determined.
- Worktree lifecycle and merge/integration strategy.
- Failure/retry policy when a role agent fails or a review rejects.
- How the orchestrator reviews work against the spec (rubric, automated checks).
