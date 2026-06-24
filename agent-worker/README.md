# Agent Worker (Phase 2)

A generic worker image: mounts a project, runs Claude Code wired to the Phase 1
memory MCP server (a local stdio subprocess talking to the shared Postgres), and
selects a role overlay + scoped skills via `AGENT_ROLE`.

## Run

```bash
# From the repo root. ANTHROPIC_API_KEY and PROJECT_DIR come from the host.
export ANTHROPIC_API_KEY=sk-...
PROJECT_DIR=/path/to/project AGENT_ROLE=developer \
  docker compose run --rm agent-worker        # interactive shell; `claude` is on PATH
```

`AGENT_ROLE` is one of `developer` | `reviewer` | `design` (default `developer`).
On start the entrypoint composes `~/.claude/CLAUDE.md` (base `AGENTS.md` + role
overlay), copies the role's skills into `~/.claude/skills/`, and registers the
`memory` MCP server.

## Config

- `config/AGENTS.md` — cross-tool engineering DNA.
- `config/roles/{developer,reviewer,design}.md` — role overlays.
- `config/skills/<role>/<skill>/SKILL.md` — one role-scoped skill per role.

## Smoke tests

```bash
./agent-worker/tests/smoke.sh
```

Builds the image and verifies role compose, `claude --version`, MCP registration,
and in-container memory connectivity against the shared `db`.

> **Note (Apple Silicon):** the connectivity check uses the in-process `local`
> fastembed provider rather than the TEI `embeddings` service, because the Phase 1
> `text-embeddings-inference:cpu-1.5` image has no `linux/arm64` manifest. The
> running stack can still point `CODE_EMBED_*`/`DOC_EMBED_*` at any reachable TEI
> or hosted embeddings endpoint.

## Memory tools

`search_code`, `search_docs`, `get_symbol`, `impact_of`, `spec_for`, `add_knowledge`
— retrieve from memory instead of re-reading the repo.
