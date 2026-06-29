# Single-Task Orchestrator — Design

- **Date:** 2026-06-29
- **Status:** Approved (ready for implementation plan)
- **Phase:** Q2, increment 1 — the first vertical slice of the orchestration layer
  (Q2–Q4). Builds on [Q1 per-project snapshot memory](2026-06-25-per-project-snapshot-memory-design.md).

## Problem

Q1 gave the system per-project, snapshot-able memory. The agent-worker exists as
an **interactive** container (validates `AGENT_ROLE`, composes `AGENTS.md` + role
overlay into `~/.claude/CLAUDE.md`, enables role-scoped skills, registers the
memory MCP, then `exec`s a command). What is missing is anything that *drives* an
agent: there is no orchestrator, no `tasks` table, no way to hand a task to a role
and collect the result.

The full orchestration layer (universal entrypoint → decompose → parallel role
agents → review → teardown) is three buildable sub-projects (warm containers →
orchestrator + `tasks` → parallel worktree fan-out). This spec is the **first
vertical slice**: take **one** task end-to-end through a bounded
developer→reviewer→iterate loop, proving the `exec` + coordinate-through-`tasks` +
collect + snapshot + teardown machinery on the simplest case. Multi-task
decomposition, role inference, and parallel fan-out are explicitly deferred to
later increments.

## What it does

```
agentctl run --role developer path/to/task.md
```

A deterministic **host-side Python orchestrator** brings up the project's stack
(reusing Q1 startup to restore-or-seed memory), runs the task on a dedicated
branch through a developer agent, has a reviewer agent check it, auto-iterates up
to a bounded number of rounds, then dumps the snapshot and tears down — reporting
the branch, what changed, and the final review verdict.

## Decisions (resolved during brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Increment scope | **Vertical slice**: one task end-to-end, not the whole orchestrator. |
| 2 | Entry point | **Task file + explicit role**: `agentctl run --role developer path/to/task.md`. No role inference, no decomposition. |
| 3 | Work location & output | **Dedicated task branch** `feat/<id>-<tagline>` in `/workspace`; the orchestrator reports the branch + diff. No worktrees yet. |
| 4 | Shape of work | **Developer→reviewer gate**: worker role runs the task, then a reviewer agent checks the same branch. |
| 5 | On `needs_changes` | **Bounded auto-iterate** (cap default 2 rounds): re-run the developer with the reviewer's notes, then report. |
| 6 | Agent driver | **Thin Claude Agent SDK runner** (Python owns the session), honoring the spec's "Python + Agent SDK, not LLM-driven-lifecycle" decision. Not headless `claude -p`. |
| 7 | Orchestrator nature | **Plain host-side Python**, not an LLM. Drives `docker compose` + `docker exec`; resolves the project via `pwd`. |
| 8 | Per-role authoring | **Reuse existing role config** (`CLAUDE.md` + scoped skills). Bespoke per-role domain authoring is its own later brainstorm. |

## Architecture & components

The orchestrator is **plain Python on the host** (the "orchestrator agent" is a
later concern). It drives `docker compose` and `docker exec`; running host-side
(resolving the project via `pwd`, per the spec's `agentctl run` sketch) avoids
docker-in-docker.

Each unit has one clear responsibility behind a well-defined interface:

1. **`agentctl` CLI** (host) — parses `--role` + task file, invokes the
   orchestrator. Thin.
2. **Orchestrator control loop** (host Python) — lifecycle (compose up → loop →
   dump → down), the bounded dev→review→iterate loop, and `tasks`-row updates.
   The testable heart; docker/agent calls sit behind seams (below) so the loop
   logic is unit-testable with them faked.
3. **`tasks` table + `TaskRepository`** (in `memory-service`, same Postgres memory
   DB) — coordination state; snapshotted/restored by Q1, so it travels with the
   project.
4. **Role SDK runner** (in the agent-worker image) — one generic Python program
   using the Claude Agent SDK, exec'd into a warm container; parameterized by
   role + task-id + round; runs the session under Python control, commits (dev)
   or produces a verdict (reviewer), and writes results to its `tasks` row.
5. **Warm role containers** — `developer` and `reviewer` as long-lived compose
   services (the existing entrypoint already composes their `CLAUDE.md` + skills +
   memory MCP at start); the orchestrator `exec`s the runner into them per round.
6. **Branch helper** (host) — creates/reports the `feat/<id>-<tagline>` branch.

### Two seams (what makes the loop testable)

- **Agent invocation seam** — `exec_runner(role, task_id, round) -> RunnerResult`
  wraps `docker exec … python -m agent.runner …`. The orchestrator loop is written
  against this interface, so round/gating/termination logic is unit-testable with
  the runner faked — no containers in those tests.
- **Memory/lifecycle seam** — `compose up` / `snapshot dump` / `compose down`
  reuse the Q1 entrypoints unchanged.

## Control flow & data flow

```
agentctl run --role developer ./tasks/add-export.md
  1. resolve project (pwd) → PROJECT_DIR; read task file; derive tagline from its H1
  2. docker compose up  → db, embeddings, init (Q1 startup: restore-or-seed memory),
                          developer + reviewer warm containers (depends_on init done)
  3. INSERT tasks row {spec_ref, title, assignee_role=developer, status=in_progress,
                       round=0, review_status=pending} → returns id
  4. branch helper: git -C /workspace checkout -b feat/<id>-<tagline> from base;
                    UPDATE tasks.branch
  5. round loop (round = 1..CAP, default 2):
       a. exec developer runner in dev container (inputs: task file, branch, round,
          latest review_notes [empty on round 1]):
            → edits /workspace on the branch, commits, add_knowledge,
              writes summary + artifacts (commit shas) + status to its tasks row
       b. exec reviewer runner in reviewer container (inputs: branch diff + task file):
            → writes review_status ∈ {approved, needs_changes} + review_notes to the row
       c. approved?                      → break
          needs_changes & round < CAP    → loop, feeding notes to the developer
          needs_changes & round == CAP   → stop: status=needs_changes (cap reached)
  6. memory.snapshot dump   (Q1 hook — preserve add_knowledge + tasks state)
  7. docker compose down    (only after a verified dump)
  8. report: branch, commit list, final review_status + notes, rounds used, summary
```

**Agents coordinate through the `tasks` row + the branch, not direct messaging:**
the developer commits to the branch and records its result; the reviewer reads the
branch diff + task file and writes its verdict/notes back to the row; on iterate,
the developer reads `review_notes` from the row. This is the spec's
"coordinate through the `tasks` table + shared memory" pattern at minimal scale.

## The `tasks` table

A new table in the **same memory Postgres**, so coordination state is
snapshotted/restored by Q1 and travels with the project. Aligned with the
orchestration appendix's eventual shape `(id, spec_ref, status, assignee_role,
branch, summary, review_status, artifacts)`:

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_ref      TEXT NOT NULL,                         -- the task file path
    title         TEXT NOT NULL,                         -- derived from the task file's H1
    assignee_role TEXT NOT NULL,                         -- worker role, e.g. 'developer'
    branch        TEXT,                                  -- feat/<id>-<tagline>, set after insert
    status        TEXT NOT NULL DEFAULT 'in_progress',   -- in_progress|approved|needs_changes|failed
    round         INT  NOT NULL DEFAULT 0,               -- rounds consumed (cap default 2)
    review_status TEXT NOT NULL DEFAULT 'pending',       -- pending|approved|needs_changes
    review_notes  TEXT,                                  -- reviewer feedback (fed back on iterate)
    summary       TEXT,                                  -- developer's "what changed"
    artifacts     JSONB NOT NULL DEFAULT '[]',           -- commit shas / changed files
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

Applied alongside the existing `001_schema.sql` (so `memory.startup` / `reconcile`
create it too) and added to the test-fixture truncation list. A small
`TaskRepository` (mirroring the existing `Repository`) handles: insert,
set-branch, record-developer-result, record-review, advance-round.

### Branch naming

Resolves the desired `feat/<task-id>:<tagline>` into a **valid git refname**
(refnames cannot contain `:`):

- Insert the row first to obtain `id`.
- Branch = `feat/<id>-<tagline>`, where `<tagline>` is the task file's H1
  kebab-cased and length-capped (e.g. *"Add CSV export"* → `feat/42-add-csv-export`).
- Falls back to the task filename stem if there is no H1; sanitized to `[a-z0-9-]`.

## The role SDK runner

`python -m agent.runner --role <r> --task-id <id> --round <n>`, exec'd into the
matching warm container. The agent-worker image already carries the memory Python
env (`/opt/memory`) and the role's composed `~/.claude/CLAUDE.md` + scoped skills +
memory MCP, so the runner inherits the role's instructions and tools.

**Driven by the Claude Agent SDK under Python control:** the runner owns the
session — role `CLAUDE.md` as the system prompt, scoped tools (memory MCP + file
edit + bash + git), a programmatic stop condition, and authentication via the
existing `CLAUDE_CODE_OAUTH_TOKEN` (subscription) injected into the container.
Full in-sandbox permissions, per the spec.

**Role-branched behavior:**
- **developer:** reads the task file (and `review_notes` from the `tasks` row when
  `round > 1`), works in `/workspace` on the task branch, commits, calls
  `add_knowledge`, then writes `summary` + `artifacts` + `status` to its row.
- **reviewer:** reads the branch diff + task file, produces a verdict, writes
  `review_status` + `review_notes` to the row. Does **not** commit code.

**Result handoff = the `tasks` row, via the shared `TaskRepository`** (imported
from the memory package the image ships): agents "update their row," per the spec.
The orchestrator reads the row back after each `exec`; the `exec` exit code signals
run-level success/failure while the row carries the content.

**Scope boundary:** the runner is generic and reuses the *existing* role config;
bespoke per-role domain authoring is its own later brainstorm.

## Compose / image changes

- **Warm role containers:** add `developer` and `reviewer` as long-lived services
  (e.g. command keeps them alive), each with its `AGENT_ROLE`, the project mounted
  read-write at `/workspace`, `CLAUDE_CODE_OAUTH_TOKEN`, and `DATABASE_URL`. Both
  gate on the Q1 `init` completing (like other DB consumers).
- **agent-worker image:** add the Claude Agent SDK and the `agent.runner` module.
- The existing single interactive agent-worker service remains for manual use.

## Error handling

Guiding rule: **never tear down lossy, never iterate forever.**

| Condition | Behavior |
|-----------|----------|
| Reviewer returns `needs_changes` at the round cap | Terminal `needs_changes` (a normal outcome, **not** failure): report verdict + notes + branch. |
| Developer/reviewer runner exits non-zero (crash) | Mark task `failed`, keep whatever was committed, **still attempt the Q1 snapshot dump**, then down; report failure + branch. |
| Snapshot `dump` fails (Q1 contract) | **Do not** `compose down`; surface loudly; leave the stack up (memory not safely persisted). |
| Branch creation fails (dirty `/workspace`, name exists) | Abort early with a clear message before any agent runs. |
| `compose up` / Q1 `init` fails | Abort; nothing to dump; report. |

No mid-run resume in this slice (YAGNI): an interrupted run leaves the `tasks` row
+ branch in place; re-running starts a fresh task.

## Testing

- **Orchestrator loop** (unit, agent-invocation seam faked — no containers):
  approve-round-1 (one cycle); needs_changes→approve (two cycles, notes fed into
  round 2); needs_changes×2 at cap (terminal `needs_changes`); runner-failure
  (status `failed`, dump still attempted, never down-before-dump).
- **`TaskRepository`** (against the live pg18 DB, like existing repo tests):
  insert, set-branch, record-developer-result, record-review, advance-round.
- **Branch-name derivation** (pure unit): H1→slug, filename-stem fallback,
  sanitize to `[a-z0-9-]`, length cap, no `:`.
- **Runner** (unit, SDK call faked): arg parsing, role dispatch, writing results to
  the `tasks` row.
- **Q1 round-trip extension:** `tasks` rows survive `dump`→`restore`.
- **End-to-end smoke** (scripted, like phase-2's `smoke.sh`, not the pytest unit
  suite): `agentctl run --role developer <trivial task>` on a throwaway project →
  branch created, a commit exists, `tasks` row terminal, snapshot dumped, stack down.

## Out of scope (deferred to later increments)

- **Multi-task decomposition / role inference** — a planning step that splits a
  spec into independent tasks and routes roles. This slice takes one task + an
  explicit role.
- **Parallel fan-out + git worktrees** — N developers in parallel; the worktree
  lifecycle and integration/merge strategy (Q4).
- **The `design` role in the loop** — only developer + reviewer participate here.
- **Bespoke per-role domain authoring** — how the user expresses domain knowledge
  into each agent; its own brainstorm.
- **Mid-run resume / crash recovery** and **richer retry policy** beyond the round
  cap.
- **Auto-merge of the task branch** — the human reviews/merges the reported branch.

## Dependency & build order (within the orchestration layer)

1. **Q1 — per-project snapshot memory** (done).
2. **This slice — single-task orchestrator** (warm dev+reviewer containers + `exec`
   + `tasks` table + bounded dev→review→iterate loop, one task).
3. Multi-task decomposition + the full orchestrator.
4. Parallel fan-out with git worktrees.
