# Phase 2 (Core Worker) — Agent Worker Image Design Spec

- **Status:** Draft for review
- **Date:** 2026-06-24
- **Builds on:** Phase 1 + 1.5 memory service (merged to `main`).
- **Branch:** `feat/agent-worker-image`
- **Parent design:** [Agent Dev Workspace](2026-06-24-agent-dev-workspace-design.md) §5.1 (worker image), §5.3 (config hierarchy), §5.5 (wiring).

---

## 1. Problem

Phase 1/1.5 built the **persistent plane** (the memory service). There is no **ephemeral plane** yet — no container an agent actually runs in. Phase 2 (core) delivers that: a reproducible worker image where a role-scoped agent runs against a bind-mounted project, wired to the memory layer's MCP tools.

## 2. Goals / non-goals

**Goals**
- One **generic worker image** that mounts a project and runs an agent CLI wired to the memory MCP tools.
- **Role-based** configuration (`developer` / `reviewer` / `design`) selecting the active instruction overlay + scoped skills.
- A **root compose** that runs the whole system: `db` + `embeddings` + ingest `worker` + `agent-worker`.
- Memory is **shared across sessions/workers** (the agent's MCP server is a local stdio subprocess talking to the shared Postgres).

**Non-goals (deferred)**
- Toolset bootstrap-from-spec; orchestration stub; hard governance gating (`policies.yaml`); multi-CLI (Gemini); headless browser; baked language toolchains.

## 3. Constraints / decisions

| Decision | Value | Rationale |
|---|---|---|
| Host | macOS Apple Silicon (arm64) | Build `linux/arm64`. |
| Bundled CLI | **Claude Code only** | Reference client; the MCP server + `AGENTS.md` stay tool-agnostic. Concrete + smoke-testable now. |
| Memory transport | **bundled stdio MCP subprocess** | Reuses Phase 1's `mcp_server` verbatim; shared DB = shared memory. No new transport code. |
| Role scoping | **soft / instructional** | Overlay tells the agent its job + skills; hard tool-gating is the deferred governance phase. |
| Skills in v1 | **one representative skill per role** | YAGNI; full libraries later. |
| Browser | **deferred** | Chromium is heavy and not needed for the agent↔memory core. |
| Auth | runtime env (`ANTHROPIC_API_KEY`) | Never baked into the image. |
| Language toolchains | **not baked** | Bootstrap-from-spec is a later phase. |

## 4. Architecture

Two planes (parent design): the **persistent plane** is the existing memory service (`db` + `embeddings`, on a named volume). The new **ephemeral plane** is the `agent-worker` container. The agent CLI inside it spawns the memory MCP server as a **local stdio subprocess**, which connects over the compose network to the shared `db` and `embeddings`. The worker container is disposable; all durable state lives in the persistent plane.

```
root docker-compose.yml
├── db           (pgvector)        [persistent: pgdata volume]
├── embeddings   (fastembed HTTP service)
├── worker       (ingest: drains queue)         ← Phase 1
└── agent-worker (Claude Code + memory MCP)     ← Phase 2
      AGENT_ROLE=developer|reviewer|design
      mounts ${PROJECT_DIR} -> /workspace
      spawns `python -m memory.mcp_server` (stdio) -> db + embeddings
```

## 5. Components

### 5.1 Worker image (`agent-worker/Dockerfile`)
- **Base:** Debian-slim with **Node** (Claude Code is an npm package, needs Node 18+) **+ Python 3.12 + `uv`** (to run the memory MCP server). `linux/arm64`.
- **Memory package:** install the Phase 1 `memory` package from the repo build context (so `python -m memory.mcp_server` is runnable in-container).
- **Universal tools:** `git`, `gh`, `ripgrep`, `fd`, `curl`, `jq`. No language runtimes.
- **Agent CLI:** Claude Code installed globally (`npm i -g @anthropic-ai/claude-code`). Auth via `ANTHROPIC_API_KEY` at runtime.
- **User:** non-root.

**Trade-offs:** carrying both Node and Python plus the memory package makes the image larger than a single-runtime image, but these are the *agent runtime* + *memory client*, not project toolchains — the lean-image principle (deferring language toolchains) still holds. Deferring the browser keeps the image materially smaller for v1.

**Resources:** Claude Code docs (https://docs.claude.com/en/docs/claude-code), MCP (https://modelcontextprotocol.io), Docker multi-arch (https://docs.docker.com/build/building/multi-platform/).

### 5.2 Config hierarchy (`agent-worker/config/`)
Baked into the image:
- `AGENTS.md` — cross-tool base "engineering DNA": spec-first/BDD discipline, propose-structure-before-coding, fix-root-cause-only, version-pinning, context-hygiene, and the memory-tool usage habit (retrieve via MCP, don't re-dump).
- `roles/developer.md`, `roles/reviewer.md`, `roles/design.md` — role overlays (the role's purpose + which skills it has).
- `skills/` — role-scoped skills (v1: `reviewer` → a `code-check` skill mirroring the whitepaper's review criteria; `developer` → a `scaffold` skill; `design` → a `ui` stub).

**Trade-offs:** `AGENTS.md` as the cross-tool base (rather than a Claude-only file) preserves the "any MCP client" promise; the entrypoint adapts it into Claude Code's expected location at runtime.

**Resources:** `AGENTS.md` convention (https://agents.md), Gherkin/BDD (https://cucumber.io/docs/gherkin/reference).

### 5.3 Entrypoint + role mechanism (`agent-worker/entrypoint.sh`)
On container start:
1. Read `AGENT_ROLE` (default `developer`); validate it is one of the three.
2. **Compose instructions:** concatenate `AGENTS.md` + `roles/$AGENT_ROLE.md` → write to `~/.claude/CLAUDE.md` (global memory, so the mounted project is never written to).
3. **Register the memory MCP server** in Claude Code's user config as a stdio server: command `python -m memory.mcp_server`, env `DATABASE_URL`, `CODE_EMBED_*`, `DOC_EMBED_*` (pointing at `db`/`embeddings`). (Implemented via `claude mcp add` or by writing the user `~/.claude.json` MCP entry.)
4. **Enable role skills:** make `skills/` for `$AGENT_ROLE` available to Claude Code (copy into the user skills location).
5. `cd /workspace` and exec the requested command (default: an interactive shell with `claude` on `PATH`; `claude` can also be launched directly).

**Trade-offs:** writing the composed instructions to the global `~/.claude/CLAUDE.md` keeps the user's repo clean at the cost of "global" semantics inside the container — acceptable because the container is single-purpose and disposable.

### 5.4 Compose wiring (root `docker-compose.yml`)
A root compose orchestrating the full system. Reuses the Phase 1 `db`, `embeddings`, and ingest `worker` service definitions, and adds `agent-worker`:
- `build:` the `agent-worker/` image (build context must include the `memory-service` package for installation).
- `depends_on:` `db` (and `embeddings`).
- `environment:` `DATABASE_URL`, `CODE_EMBED_*`/`DOC_EMBED_*` (→ `db`/`embeddings`), `AGENT_ROLE`, `ANTHROPIC_API_KEY` (passed through from the host).
- `volumes:` `${PROJECT_DIR:-./}:/workspace`.
- Run via `docker compose run --rm agent-worker` for an interactive session.

**Trade-offs:** a root compose (vs extending `memory-service/docker-compose.yml`) cleanly expresses the whole system and keeps the memory service's own compose self-contained for its tests.

## 6. Data flow

`docker compose run agent-worker` → entrypoint composes role config + registers the memory MCP server → agent runs in `/workspace` → on a memory query, Claude Code spawns `python -m memory.mcp_server` (stdio) → that process queries the shared `db` (+ `embeddings` for query embeddings) → results return to the agent. Ingestion continues independently via the Phase 1 `worker` + git hook.

## 7. Testing (smoke, not unit)

This phase is an image + config, so verification is scripted build/run smoke checks (e.g. `agent-worker/tests/smoke.sh`):
1. Image builds (`docker compose build agent-worker`).
2. Role compose: with `AGENT_ROLE=reviewer`, the entrypoint produces a `~/.claude/CLAUDE.md` containing both the base `AGENTS.md` marker and the reviewer overlay marker; an invalid role fails fast.
3. `claude --version` succeeds in-container.
4. `claude mcp list` shows a `memory` server.
5. In-container memory connectivity: a script connects the bundled `memory` package to the shared `db` and a `search_code` call returns without error (against a seeded or empty index).

## 8. Out of scope / escalation paths

- **Bootstrap-from-spec** (install project toolchain from `specs/`) — next sub-phase.
- **Orchestration stub** (`tasks` table + role handoff) — next sub-phase.
- **Hard governance** (`policies.yaml` structural + semantic gating, egress allowlist enforcement) — governance phase.
- **Multi-CLI** (Gemini CLI) — additive (second MCP config + install).
- **Headless browser** for E2E/visual verification — additive image layer.
