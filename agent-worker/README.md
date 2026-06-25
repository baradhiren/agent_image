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

> **Note (Apple Silicon):** the default `embeddings` service is now an
> arm64-native fastembed HTTP service (`memory.embeddings_server`), because the
> original `text-embeddings-inference:cpu-1.5` image has no `linux/arm64`
> manifest. The running stack works on Apple Silicon out of the box; point
> `CODE_EMBED_*`/`DOC_EMBED_*` at TEI or any hosted endpoint if you prefer.

## Toolset bootstrap (Phase 3)

The image bakes `mise` but no language runtimes. At session start the entrypoint
runs `bootstrap.sh` against `/workspace` (unless `BOOTSTRAP=skip`):

- **Runtimes** — pin them in the project's `.mise.toml` or `.tool-versions`
  (e.g. `node 20.11.1`). `mise install` + `mise reshim` make them active on `PATH`.
- **Setup** — optional `specs/toolset.yaml` with an ordered `setup:` list of shell
  commands (e.g. `pnpm install`, `uv sync`) run after runtimes install.

Toolchains are cached in the `mise-data` named volume across runs. Bootstrap is
**warn-and-continue**: a failure prints a warning rather than ending the session.
Re-run any time with the **bootstrap** skill (`bootstrap.sh`) after editing the
toolset or when a build fails on a missing tool.

## Memory tools

`search_code`, `search_docs`, `get_symbol`, `impact_of`, `spec_for`, `add_knowledge`
— retrieve from memory instead of re-reading the repo.
