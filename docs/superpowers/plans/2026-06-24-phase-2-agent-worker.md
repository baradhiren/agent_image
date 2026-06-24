# Phase 2 (Core Worker) — Agent Worker Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a generic `agent-worker` Docker image that mounts a project, runs Claude Code wired to the Phase 1 memory MCP server as a local stdio subprocess, and selects a role overlay + scoped skills via `AGENT_ROLE`; plus a root compose that runs the whole system.

**Architecture:** A `python:3.12-slim` base gains Node 20 (for the Claude Code npm package) and an **editable install** of the existing `memory` package (so its `mcp_server` runs in-container against the shared `db`/`embeddings`). Baked config (`AGENTS.md` + three role overlays + role-scoped skills) is composed at container start by `entrypoint.sh`, which writes a global `~/.claude/CLAUDE.md`, copies the role's skills into `~/.claude/skills/`, and registers the memory MCP server with `claude mcp add`. A root `docker-compose.yml` `include`s the Phase 1 compose (`db` + `embeddings` + ingest `worker`) and adds the `agent-worker` service. Verification is **scripted build/run smoke checks**, not unit tests.

**Tech Stack:** Docker / Docker Compose, Debian-slim + Node 20 + Python 3.12 + `uv`, Claude Code (`@anthropic-ai/claude-code`), the Phase 1 `memory` package, Bash.

## Global Constraints

- Host is macOS Apple Silicon: images build `linux/arm64` by default (no `--platform` flag needed).
- All new files live under `agent-worker/`, **except** the root `docker-compose.yml` (repo root).
- Bundled agent CLI is **Claude Code only**. `AGENTS.md` stays tool-agnostic; the entrypoint adapts it into Claude Code's location at runtime.
- Memory transport is a **bundled stdio MCP subprocess** reusing the Phase 1 `memory` package **verbatim** (no edits to `memory-service/`). It must be an **editable install** (`uv sync`) so [db.py:8](../../../memory-service/src/memory/db.py#L8) resolves `sql/001_schema.sql` via `Path(__file__).resolve().parents[2] / "sql"`.
- Roles are exactly `developer` | `reviewer` | `design`; default `developer`; an invalid role **fails fast**.
- Role scoping is **soft/instructional** (overlay + skills), not hard tool-gating.
- One representative skill per role: `developer` → `scaffold`, `reviewer` → `code-check`, `design` → `ui`.
- Auth (`ANTHROPIC_API_KEY`) is **runtime env only**, never baked into the image.
- No language toolchains, no browser baked in (deferred).
- Container runs as **non-root** user `agent` (home `/home/agent`).
- The Docker build context is the **repo root** (the Dockerfile copies from both `memory-service/` and `agent-worker/`). Standalone builds: `docker build -f agent-worker/Dockerfile .` from repo root.
- Memory MCP command inside the container: `/opt/memory/.venv/bin/python -m memory.mcp_server` (exposed as `$MEMORY_PYTHON`). It inherits `DATABASE_URL`/`CODE_EMBED_*`/`DOC_EMBED_*` from the container environment.
- Verification is **smoke (build/run), not unit tests**. Commit after each task.

---

### Task 1: Base config — engineering DNA + role overlays

**Files:**
- Create: `agent-worker/config/AGENTS.md`
- Create: `agent-worker/config/roles/developer.md`
- Create: `agent-worker/config/roles/reviewer.md`
- Create: `agent-worker/config/roles/design.md`

**Interfaces:**
- Produces: four Markdown files. `AGENTS.md` contains the literal marker line `# Engineering DNA` (smoke greps for `Engineering DNA`). Each role overlay's first line is `# Role: Developer` / `# Role: Reviewer` / `# Role: Design` (reviewer smoke greps for `Role: Reviewer`). These exact paths and markers are consumed by `entrypoint.sh` (Task 3) and the smoke checks (Task 6).

- [ ] **Step 1: Create `agent-worker/config/AGENTS.md`**

```markdown
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
```

- [ ] **Step 2: Create `agent-worker/config/roles/developer.md`**

```markdown
# Role: Developer

You build features. Default role.

- Start from the spec. Propose structure + stack, then implement against
  BDD scenarios.
- Use `search_code` / `get_symbol` / `impact_of` before changing shared code,
  so you understand blast radius instead of guessing.
- Available skill: **scaffold** — stand up a new module spec-first.
```

- [ ] **Step 3: Create `agent-worker/config/roles/reviewer.md`**

```markdown
# Role: Reviewer

You audit a diff. You do not add features.

- Review for correctness, security, and logic — not style nits.
- Use `impact_of` to check what a changed symbol affects, and `spec_for` to
  confirm the change matches its spec.
- Available skill: **code-check** — the structured security + logic review pass.
```

- [ ] **Step 4: Create `agent-worker/config/roles/design.md`**

```markdown
# Role: Design

You work on UI and visual design.

- Translate specs into interface structure and components.
- Available skill: **ui** — UI scaffolding stub (expanded in a later phase).
```

- [ ] **Step 5: Verify the files exist with the expected markers**

Run:
```bash
cd "$(git rev-parse --show-toplevel)"
grep -q "Engineering DNA" agent-worker/config/AGENTS.md && echo "AGENTS.md ok"
grep -q "Role: Developer" agent-worker/config/roles/developer.md && echo "developer ok"
grep -q "Role: Reviewer" agent-worker/config/roles/reviewer.md && echo "reviewer ok"
grep -q "Role: Design"   agent-worker/config/roles/design.md && echo "design ok"
```
Expected: four `... ok` lines.

- [ ] **Step 6: Commit**

```bash
git add agent-worker/config/AGENTS.md agent-worker/config/roles/
git commit -m "feat(agent-worker): add engineering-DNA base + role overlays"
```

---

### Task 2: Role-scoped skills (one per role)

**Files:**
- Create: `agent-worker/config/skills/developer/scaffold/SKILL.md`
- Create: `agent-worker/config/skills/reviewer/code-check/SKILL.md`
- Create: `agent-worker/config/skills/design/ui/SKILL.md`

**Interfaces:**
- Produces: per-role skill directories under `config/skills/<role>/`. Each skill is a folder containing a `SKILL.md` with YAML frontmatter (`name`, `description`). `entrypoint.sh` (Task 3) copies the **contents** of `config/skills/$AGENT_ROLE/` into `~/.claude/skills/`, so `reviewer` yields `~/.claude/skills/code-check/SKILL.md`. The reviewer smoke check (Task 6) asserts `code-check/SKILL.md` exists after a reviewer run.

- [ ] **Step 1: Create `agent-worker/config/skills/developer/scaffold/SKILL.md`**

```markdown
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
```

- [ ] **Step 2: Create `agent-worker/config/skills/reviewer/code-check/SKILL.md`**

```markdown
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
```

- [ ] **Step 3: Create `agent-worker/config/skills/design/ui/SKILL.md`**

```markdown
---
name: ui
description: Use when building or shaping UI - a stub for translating specs into interface structure and components (expanded in a later phase).
---

# UI (stub)

Translate the spec's interface requirements into component structure:

1. Read the relevant spec section (`search_docs`).
2. Propose the component hierarchy and the states each component must handle
   (loading / empty / error / populated).
3. Implement against those states.

This is a v1 stub; the full design skill library lands in a later phase.
```

- [ ] **Step 4: Verify the skill tree**

Run:
```bash
cd "$(git rev-parse --show-toplevel)"
for f in developer/scaffold reviewer/code-check design/ui; do
  test -f "agent-worker/config/skills/$f/SKILL.md" && echo "$f ok"
done
grep -q "name: code-check" agent-worker/config/skills/reviewer/code-check/SKILL.md && echo "frontmatter ok"
```
Expected: `developer/scaffold ok`, `reviewer/code-check ok`, `design/ui ok`, `frontmatter ok`.

- [ ] **Step 5: Commit**

```bash
git add agent-worker/config/skills/
git commit -m "feat(agent-worker): add one role-scoped skill per role"
```

---

### Task 3: Entrypoint + role mechanism

**Files:**
- Create: `agent-worker/entrypoint.sh`
- Test (throwaway local harness, not committed): `/private/tmp/claude-504/-Users-hirenppp-Documents-Claude-Escapades-agent-image/92f01994-3dfd-4fdd-86aa-291ae0923830/scratchpad/test_entrypoint.sh`

**Interfaces:**
- Consumes: `config/AGENTS.md`, `config/roles/$AGENT_ROLE.md`, `config/skills/$AGENT_ROLE/` (Tasks 1–2), via `$CONFIG_DIR` (default `/opt/agent-worker/config`).
- Produces: `entrypoint.sh` that (a) validates `AGENT_ROLE` ∈ {developer,reviewer,design}, exits non-zero otherwise; (b) writes `$HOME/.claude/CLAUDE.md` = `AGENTS.md` + role overlay concatenated; (c) copies the role's skills into `$HOME/.claude/skills/`; (d) registers the memory MCP server via `claude mcp add memory --scope user -- $MEMORY_PYTHON -m memory.mcp_server` when `claude` is on `PATH`; (e) `cd $WORKSPACE` then `exec "$@"`. Overridable env for testing: `CONFIG_DIR`, `WORKSPACE` (default `/workspace`), `MEMORY_PYTHON` (default `/opt/memory/.venv/bin/python`), `HOME`.

- [ ] **Step 1: Write the local test harness**

Create `…/scratchpad/test_entrypoint.sh` (the scratchpad dir from the environment header). It stubs `claude` so the entrypoint can be exercised without Docker, and points `CONFIG_DIR` at the repo config:

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
ENTRY="$REPO/agent-worker/entrypoint.sh"

WORK="$(mktemp -d)"
HOME_DIR="$WORK/home"
BIN="$WORK/bin"
mkdir -p "$HOME_DIR" "$BIN"

# Stub `claude` so `claude mcp add` is a no-op that succeeds.
cat > "$BIN/claude" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$BIN/claude"

run() {  # run <role> <cmd...>
  local role="$1"; shift
  env -i PATH="$BIN:/usr/bin:/bin" HOME="$HOME_DIR" \
    CONFIG_DIR="$REPO/agent-worker/config" WORKSPACE="$WORK" \
    AGENT_ROLE="$role" bash "$ENTRY" "$@"
}

# 1. valid role composes CLAUDE.md from base + overlay, copies skills
rm -rf "$HOME_DIR/.claude"
run reviewer true
grep -q "Engineering DNA" "$HOME_DIR/.claude/CLAUDE.md"
grep -q "Role: Reviewer"  "$HOME_DIR/.claude/CLAUDE.md"
test -f "$HOME_DIR/.claude/skills/code-check/SKILL.md"
echo "PASS: reviewer composes CLAUDE.md + skills"

# 2. invalid role fails fast
if run bogus true 2>/dev/null; then
  echo "FAIL: invalid role did not exit non-zero"; exit 1
fi
echo "PASS: invalid role fails fast"

rm -rf "$WORK"
echo "ALL PASS"
```

- [ ] **Step 2: Run the harness to verify it FAILS**

```bash
bash "/private/tmp/claude-504/-Users-hirenppp-Documents-Claude-Escapades-agent-image/92f01994-3dfd-4fdd-86aa-291ae0923830/scratchpad/test_entrypoint.sh"
```
Expected: FAIL — `bash: …/agent-worker/entrypoint.sh: No such file or directory` (entrypoint not written yet).

- [ ] **Step 3: Create `agent-worker/entrypoint.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${CONFIG_DIR:-/opt/agent-worker/config}"
WORKSPACE="${WORKSPACE:-/workspace}"
MEMORY_PYTHON="${MEMORY_PYTHON:-/opt/memory/.venv/bin/python}"
AGENT_ROLE="${AGENT_ROLE:-developer}"
HOME_DIR="${HOME:-/home/agent}"

# 1. Validate the role.
case "$AGENT_ROLE" in
  developer|reviewer|design) ;;
  *)
    echo "entrypoint: invalid AGENT_ROLE '$AGENT_ROLE' (expected developer|reviewer|design)" >&2
    exit 1
    ;;
esac

mkdir -p "$HOME_DIR/.claude/skills"

# 2. Compose instructions: base engineering DNA + role overlay -> global CLAUDE.md
#    (global, so the mounted project is never written to).
cat "$CONFIG_DIR/AGENTS.md" "$CONFIG_DIR/roles/$AGENT_ROLE.md" > "$HOME_DIR/.claude/CLAUDE.md"

# 3. Enable the role's scoped skills.
if [ -d "$CONFIG_DIR/skills/$AGENT_ROLE" ]; then
  cp -r "$CONFIG_DIR/skills/$AGENT_ROLE/." "$HOME_DIR/.claude/skills/"
fi

# 4. Register the memory MCP server (stdio subprocess; inherits DB/embeddings env).
if command -v claude >/dev/null 2>&1; then
  claude mcp remove memory --scope user >/dev/null 2>&1 || true
  claude mcp add memory --scope user -- "$MEMORY_PYTHON" -m memory.mcp_server
fi

# 5. Work in the mounted project; run the requested command (default: bash).
cd "$WORKSPACE" 2>/dev/null || true
exec "$@"
```

- [ ] **Step 4: Run the harness to verify it PASSES**

```bash
bash "/private/tmp/claude-504/-Users-hirenppp-Documents-Claude-Escapades-agent-image/92f01994-3dfd-4fdd-86aa-291ae0923830/scratchpad/test_entrypoint.sh"
```
Expected: `PASS: reviewer composes CLAUDE.md + skills`, `PASS: invalid role fails fast`, `ALL PASS`.

- [ ] **Step 5: Syntax-check and commit**

```bash
bash -n agent-worker/entrypoint.sh && echo "syntax ok"
git add agent-worker/entrypoint.sh
git commit -m "feat(agent-worker): add role-composing entrypoint"
```

---

### Task 4: Worker Dockerfile (build + in-container smoke)

**Files:**
- Create: `agent-worker/Dockerfile`

**Interfaces:**
- Consumes: `agent-worker/config/` + `agent-worker/entrypoint.sh` (Tasks 1–3); `memory-service/{pyproject.toml,uv.lock,src,sql}` (Phase 1, unchanged).
- Produces: an image whose `ENTRYPOINT` is `/usr/local/bin/entrypoint.sh` and default `CMD` is `bash`. Bakes `CONFIG_DIR=/opt/agent-worker/config` and `MEMORY_PYTHON=/opt/memory/.venv/bin/python`. `claude` is on `PATH`; `/opt/memory/.venv` is an editable install of `memory`; runs as non-root user `agent`.

- [ ] **Step 1: Create `agent-worker/Dockerfile`**

```dockerfile
FROM python:3.12-slim

# Universal tools + Node 20 (Claude Code is an npm package) + GitHub CLI.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg git ripgrep fd-find jq \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update && apt-get install -y --no-install-recommends gh \
 && ln -s "$(command -v fdfind)" /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*

# uv (installs + runs the memory MCP server).
RUN pip install --no-cache-dir uv

# Claude Code CLI (the bundled reference MCP client).
RUN npm install -g @anthropic-ai/claude-code

# Memory MCP server: EDITABLE install so db.py resolves ../sql/001_schema.sql
# via Path(__file__).resolve().parents[2] / "sql".
COPY memory-service/pyproject.toml /opt/memory/pyproject.toml
COPY memory-service/uv.lock        /opt/memory/uv.lock
COPY memory-service/src            /opt/memory/src
COPY memory-service/sql            /opt/memory/sql
RUN cd /opt/memory && uv sync

# Baked role config, skills, and the entrypoint.
COPY agent-worker/config        /opt/agent-worker/config
COPY agent-worker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Non-root user.
RUN useradd --create-home --shell /bin/bash agent
USER agent
WORKDIR /workspace

ENV CONFIG_DIR=/opt/agent-worker/config \
    MEMORY_PYTHON=/opt/memory/.venv/bin/python

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
```

- [ ] **Step 2: Build the image (from the repo root, so the build context covers both dirs)**

```bash
cd "$(git rev-parse --show-toplevel)"
docker build -f agent-worker/Dockerfile -t agent-worker:smoke .
```
Expected: a successful build (this is slow — apt, NodeSource, npm, and `uv sync` pulling onnxruntime via `fastembed`).

- [ ] **Step 3: Smoke — Claude Code is installed and runnable**

```bash
docker run --rm agent-worker:smoke claude --version
```
Expected: a version string (e.g. `1.x.x (Claude Code)`), exit 0.

- [ ] **Step 4: Smoke — the memory package imports in-container**

```bash
docker run --rm agent-worker:smoke /opt/memory/.venv/bin/python -c "import memory.mcp_server; print('memory import ok')"
```
Expected: `memory import ok`.

- [ ] **Step 5: Smoke — entrypoint composes CLAUDE.md and registers the MCP server**

```bash
docker run --rm -e AGENT_ROLE=reviewer agent-worker:smoke \
  bash -c 'grep -q "Engineering DNA" ~/.claude/CLAUDE.md \
        && grep -q "Role: Reviewer" ~/.claude/CLAUDE.md \
        && test -f ~/.claude/skills/code-check/SKILL.md \
        && echo "compose ok"'
docker run --rm agent-worker:smoke claude mcp list | grep -q memory && echo "mcp registered ok"
docker run --rm -e AGENT_ROLE=bogus agent-worker:smoke true; \
  test $? -ne 0 && echo "invalid role fails ok"
```
Expected: `compose ok`, `mcp registered ok`, `invalid role fails ok`.

- [ ] **Step 6: Commit**

```bash
git add agent-worker/Dockerfile
git commit -m "feat(agent-worker): worker image (Node + Python + Claude Code + memory MCP)"
```

---

### Task 5: Root compose wiring

**Files:**
- Create: `docker-compose.yml` (repo root)

**Interfaces:**
- Consumes: `memory-service/docker-compose.yml` (Phase 1: `db`, `embeddings`, `worker`, `pgdata` volume) via Compose `include`; the `agent-worker` image (Task 4).
- Produces: an `agent-worker` service: build context repo root / `agent-worker/Dockerfile`; `depends_on` `db` (healthy) + `embeddings` (started); env `DATABASE_URL`, `CODE_EMBED_*`/`DOC_EMBED_*` (→ `db`/`embeddings`), `AGENT_ROLE` (default `developer`), `ANTHROPIC_API_KEY` (host pass-through); volume `${PROJECT_DIR:-./}:/workspace`. Run interactively via `docker compose run --rm agent-worker`.

- [ ] **Step 1: Create the root `docker-compose.yml`**

```yaml
# Root compose: the whole system. Reuses the Phase 1 memory-service stack
# (db + embeddings + ingest worker) and adds the Phase 2 agent-worker.
include:
  - memory-service/docker-compose.yml

services:
  agent-worker:
    build:
      context: .
      dockerfile: agent-worker/Dockerfile
    depends_on:
      db:
        condition: service_healthy
      embeddings:
        condition: service_started
    environment:
      DATABASE_URL: postgresql://postgres:postgres@db:5432/memory
      CODE_EMBED_PROVIDER: remote
      CODE_EMBED_URL: http://embeddings:80
      CODE_EMBED_MODEL: BAAI/bge-small-en-v1.5
      CODE_EMBED_DIM: "384"
      DOC_EMBED_PROVIDER: remote
      DOC_EMBED_URL: http://embeddings:80
      DOC_EMBED_MODEL: BAAI/bge-small-en-v1.5
      DOC_EMBED_DIM: "384"
      AGENT_ROLE: ${AGENT_ROLE:-developer}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
    volumes:
      - ${PROJECT_DIR:-./}:/workspace
    stdin_open: true
    tty: true
```

- [ ] **Step 2: Validate the merged compose config**

```bash
cd "$(git rev-parse --show-toplevel)"
docker compose config >/dev/null && echo "compose config ok"
docker compose config --services | sort
```
Expected: `compose config ok`, and the services list includes `agent-worker`, `db`, `embeddings`, `memory`, `worker`.

- [ ] **Step 3: Build the agent-worker via compose**

```bash
docker compose build agent-worker
```
Expected: a successful build (cached layers from Task 4 make this fast).

- [ ] **Step 4: Smoke — role compose through `docker compose run` (no DB needed)**

```bash
docker compose run --rm --no-deps -e AGENT_ROLE=reviewer agent-worker \
  bash -c 'grep -q "Role: Reviewer" ~/.claude/CLAUDE.md && echo "run-role ok"'
```
Expected: `run-role ok`. (`--no-deps` skips starting `db`/`embeddings` for this config-only check.)

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(agent-worker): root compose wiring the full system"
```

---

### Task 6: Smoke-test script + README

**Files:**
- Create: `agent-worker/tests/smoke.sh`
- Create: `agent-worker/README.md`

**Interfaces:**
- Consumes: everything from Tasks 1–5 (the image, compose, config, entrypoint).
- Produces: `agent-worker/tests/smoke.sh` — the durable, runnable codification of the spec's five smoke checks (build; role compose + invalid-role fail-fast; `claude --version`; `claude mcp list` shows `memory`; in-container memory connectivity against the shared `db` + `embeddings`). Exits non-zero on any failure.

- [ ] **Step 1: Create `agent-worker/tests/smoke.sh`**

```bash
#!/usr/bin/env bash
# Phase 2 smoke checks. Run from anywhere; operates from the repo root.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "== 1. build agent-worker =="
docker compose build agent-worker

echo "== 2. role compose + invalid-role fail-fast =="
docker compose run --rm --no-deps -e AGENT_ROLE=reviewer agent-worker \
  bash -c 'grep -q "Engineering DNA" ~/.claude/CLAUDE.md \
        && grep -q "Role: Reviewer" ~/.claude/CLAUDE.md \
        && test -f ~/.claude/skills/code-check/SKILL.md'
echo "  reviewer overlay + skill ok"
if docker compose run --rm --no-deps -e AGENT_ROLE=bogus agent-worker true 2>/dev/null; then
  echo "  FAIL: invalid role did not fail fast"; exit 1
fi
echo "  invalid role fails fast ok"

echo "== 3. claude --version =="
docker compose run --rm --no-deps agent-worker claude --version

echo "== 4. claude mcp list shows memory =="
docker compose run --rm --no-deps agent-worker claude mcp list | grep -q memory
echo "  memory server registered ok"

echo "== 5. in-container memory connectivity (shared db + embeddings) =="
docker compose up -d db embeddings
# Wait for TEI embeddings to load its model (host-mapped 8080).
for i in $(seq 1 60); do
  if curl -sf http://localhost:8080/health >/dev/null 2>&1; then break; fi
  sleep 2
done
docker compose run --rm agent-worker /opt/memory/.venv/bin/python - <<'PY'
from memory.config import Settings
from memory.db import connect, apply_schema
from memory.repository import Repository
from memory.embeddings.factory import build_embedder

s = Settings.from_env()
conn = connect(s)
apply_schema(conn, s.code_embed.dim, s.doc_embed.dim)
repo = Repository(conn)
repo.ensure_embedding_config("code", s.code_embed.provider, s.code_embed.model, s.code_embed.dim)
repo.ensure_embedding_config("doc", s.doc_embed.provider, s.doc_embed.model, s.doc_embed.dim)
emb = build_embedder(s.code_embed)
results = repo.search_code(emb.embed(["hello world"])[0], 5)
print("search_code ok, rows:", len(results))
PY

echo "== cleanup =="
docker compose down

echo "ALL SMOKE CHECKS PASSED"
```

- [ ] **Step 2: Make it executable and run it end-to-end**

```bash
chmod +x agent-worker/tests/smoke.sh
./agent-worker/tests/smoke.sh
```
Expected: each `== N. … ==` section prints its `ok` line and the run ends with `ALL SMOKE CHECKS PASSED`. (Section 5 needs `db` + `embeddings`; the search returns `rows: 0` against an empty index — that is success.)

- [ ] **Step 3: Create `agent-worker/README.md`**

```markdown
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
and in-container memory connectivity against the shared `db` + `embeddings`.

## Memory tools

`search_code`, `search_docs`, `get_symbol`, `impact_of`, `spec_for`, `add_knowledge`
— retrieve from memory instead of re-reading the repo.
```

- [ ] **Step 4: Commit**

```bash
git add agent-worker/tests/smoke.sh agent-worker/README.md
git commit -m "test(agent-worker): smoke checks + README"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** §5.1 worker image → Task 4; §5.2 config hierarchy (`AGENTS.md` + role overlays + skills) → Tasks 1–2; §5.3 entrypoint + role mechanism → Task 3 (validate role, compose `CLAUDE.md`, register MCP, enable skills, `cd /workspace` + exec); §5.4 root compose wiring → Task 5; §6 data flow → exercised by smoke check 5 (Task 6); §7 testing (the five smoke checks) → Task 6 `smoke.sh` (build #1, role compose + invalid-role #2, `claude --version` #3, `claude mcp list` #4, connectivity #5). §8 items (bootstrap-from-spec, orchestration stub, hard governance, multi-CLI, browser) remain out of scope.
- **Memory package reused verbatim:** no edits under `memory-service/`. Editable install (`uv sync`) is mandatory because [db.py:8](../../../memory-service/src/memory/db.py#L8) locates the schema relative to `__file__`'s grandparent; a wheel install (hatchling packages only `src/memory`) would not ship `sql/`. Called out in Global Constraints and the Task 4 Dockerfile comment.
- **Type/name consistency:** `$CONFIG_DIR` (`/opt/agent-worker/config`) and `$MEMORY_PYTHON` (`/opt/memory/.venv/bin/python`) are defined identically in the Dockerfile (Task 4) and defaulted in the entrypoint (Task 3). The MCP command `$MEMORY_PYTHON -m memory.mcp_server` matches the Phase 1 entrypoint in [memory-service/Dockerfile:11](../../../memory-service/Dockerfile#L11). Markers `Engineering DNA` / `Role: Reviewer` and the skill path `code-check/SKILL.md` are produced in Tasks 1–2 and asserted in Tasks 3/4/6. Env var names (`DATABASE_URL`, `CODE_EMBED_*`, `DOC_EMBED_*`) match [config.py:21-27](../../../memory-service/src/memory/config.py#L21-L27) and the Phase 1 compose.
- **Risk — headless `claude mcp add`:** the entrypoint assumes `claude mcp add --scope user` succeeds non-interactively without an API key (it writes config, it does not connect). Smoke checks 4/5 in Task 4 and Task 6 are exactly what surface a regression here; if first-run onboarding blocks it, handle it inside the entrypoint's registration block (e.g. a one-time config seed) and re-run the Task 4 smoke. The memory **connectivity** check (#5) deliberately bypasses Claude and runs the package directly, so it isolates DB/embeddings wiring from CLI behaviour.
- **Compose `include`:** requires Docker Compose v2.20+. The included Phase 1 file keeps its own relative build context (resolved against `memory-service/`), so its `db`/`embeddings`/`worker` definitions are reused unchanged while the root file adds only `agent-worker`.
```
