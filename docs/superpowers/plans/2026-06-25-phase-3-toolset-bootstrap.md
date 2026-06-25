# Phase 3 — Toolset Bootstrap-from-Spec Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At agent session start, install the mounted project's declared, pinned language toolchain (via `mise`) and run its optional `specs/toolset.yaml` `setup:` commands — deterministically, idempotently, cached across runs, and re-runnable via a cross-role skill.

**Architecture:** The Phase 2 worker image gains the `mise` static binary and a volume-backed `MISE_DATA_DIR`. A new dependency-free `bootstrap.sh` orchestrates `mise install` + `mise reshim` (when the project has a `.mise.toml`/`.tool-versions`) then runs the ordered `setup:` commands parsed from `specs/toolset.yaml`. The Phase 2 `entrypoint.sh` is extended to enable a new `config/skills/common/` set for every role and to invoke `bootstrap.sh` after MCP registration (gated by `BOOTSTRAP=auto|skip`, warn-and-continue on failure). The root compose adds a `mise-data` named volume and passes `BOOTSTRAP` through. No changes to `memory-service/`.

**Tech Stack:** Bash, `mise` (https://mise.jdx.dev), Docker / Docker Compose, the existing Phase 2 agent-worker image.

## Global Constraints

- Branch: `feat/toolset-bootstrap`. All changes live in `agent-worker/` and the root `docker-compose.yml`. **No edits to `memory-service/`.**
- Installer is **mise** (single static binary, arm64, non-root). No hand-rolled per-language install logic; no other installers.
- Runtime declaration is the project's native `.mise.toml` / `.tool-versions` (mise auto-discovers). Setup declaration is the optional project `specs/toolset.yaml` with a single top-level `setup:` list of shell-command strings (only `setup:` in v1; no `build:`/`test:`).
- Bootstrap must be **deterministic, idempotent, re-runnable**, and **cached** across container runs (the `mise-data` named volume).
- Failure mode is **warn and continue**, configurable via `BOOTSTRAP` env (`auto` default | `skip`). A bootstrap failure must never trap the agent out of the session.
- New `config/skills/common/` is copied for **every** role, in addition to the role's own skills.
- `bootstrap.sh` is a **thin, dependency-free Bash orchestrator**; YAML parsing handles only the defined subset (a top-level `setup:` list of strings) in pure Bash (no `yq`/`awk` dependency).
- mise stays independent of the system Node 20 (which runs Claude Code) and of the memory server's `/opt/memory/.venv`.
- Host is macOS Apple Silicon: image builds `linux/arm64`; bind-mount temp dirs from `/tmp` (Docker Desktop shares it), not `/var/folders`.
- TDD where there is logic to test (bootstrap parser/orchestrator, entrypoint gating): failing harness → verify fail → implement → verify pass → commit. Commit after each task.

---

### Task 1: `bootstrap.sh` orchestrator + fast local harness

**Files:**
- Create: `agent-worker/bootstrap.sh`
- Create (committed, durable regression test): `agent-worker/tests/bootstrap_local_test.sh`

**Interfaces:**
- Produces: `agent-worker/bootstrap.sh`, runnable as `bootstrap.sh` (it will be on `PATH` in-container). Reads `WORKSPACE` (default `/workspace`). Behavior: `cd $WORKSPACE`; if `.mise.toml` or `.tool-versions` present → `mise install` then `mise reshim`; if `specs/toolset.yaml` present → run each top-level `setup:` list command in order via `mise exec -- bash -c "<cmd>"`, aborting (non-zero exit) on the first failure; print a one-line summary; no config → print `bootstrap: no toolset config found; nothing to do` and exit 0. Setup commands run with cwd = `$WORKSPACE`.
- Consumes: `mise` on `PATH` (the harness stubs it; Task 4 bakes the real one).

- [ ] **Step 1: Write `agent-worker/tests/bootstrap_local_test.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
BOOT="$REPO/agent-worker/bootstrap.sh"

WORK="$(mktemp -d)"
BIN="$WORK/bin"
export MISE_LOG="$WORK/mise.log"
mkdir -p "$BIN"
: > "$MISE_LOG"

# Stub mise: log install/reshim/etc; pass `exec -- <cmd>` through so setup runs.
cat > "$BIN/mise" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  exec)
    shift
    [ "${1:-}" = "--" ] && shift
    exec "$@"
    ;;
  *) echo "$@" >> "$MISE_LOG" ;;
esac
EOF
chmod +x "$BIN/mise"

run() {  # run <workspace-dir>
  env PATH="$BIN:/usr/bin:/bin" MISE_LOG="$MISE_LOG" WORKSPACE="$1" bash "$BOOT"
}

# (a) mise config present -> install + reshim
P="$WORK/a"; mkdir -p "$P"; echo "node 18.20.4" > "$P/.tool-versions"
: > "$MISE_LOG"
run "$P" >/dev/null
grep -q '^install' "$MISE_LOG"
grep -q '^reshim' "$MISE_LOG"
echo "PASS: mise install + reshim on .tool-versions"

# (b) setup commands run in order
P="$WORK/b"; mkdir -p "$P/specs"
cat > "$P/specs/toolset.yaml" <<'YML'
setup:
  - echo a >> order.txt
  - echo b >> order.txt
YML
run "$P" >/dev/null
[ "$(tr -d '[:space:]' < "$P/order.txt")" = "ab" ]
echo "PASS: setup commands run in order"

# (c) abort on first failing setup command
P="$WORK/c"; mkdir -p "$P/specs"
cat > "$P/specs/toolset.yaml" <<'YML'
setup:
  - echo one >> ran.txt
  - false
  - echo three >> ran.txt
YML
if run "$P" >/dev/null 2>&1; then echo "FAIL: did not abort"; exit 1; fi
grep -q one "$P/ran.txt"
if grep -q three "$P/ran.txt" 2>/dev/null; then echo "FAIL: ran past failure"; exit 1; fi
echo "PASS: aborts on failing setup command"

# (d) no config -> no-op success
P="$WORK/d"; mkdir -p "$P"
out="$(run "$P")"
echo "$out" | grep -q "nothing to do"
echo "PASS: no-op when no config"

rm -rf "$WORK"
echo "ALL PASS"
```

- [ ] **Step 2: Run the harness to verify it FAILS**

```bash
cd "$(git rev-parse --show-toplevel)"
bash agent-worker/tests/bootstrap_local_test.sh
```
Expected: FAIL — `bash: …/agent-worker/bootstrap.sh: No such file or directory` (script not written yet).

- [ ] **Step 3: Create `agent-worker/bootstrap.sh`**

```bash
#!/usr/bin/env bash
# Deterministic, idempotent, re-runnable toolset bootstrap.
# Runtimes via mise (.mise.toml/.tool-versions); setup via specs/toolset.yaml.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
TOOLSET_YAML="$WORKSPACE/specs/toolset.yaml"

# Print each top-level `setup:` list item (one shell command per line).
# Minimal, well-defined subset: a top-level `setup:` key followed by `- ` items.
parse_setup() {
  local in_setup=0 line item
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    if [[ $line == setup:* ]]; then
      in_setup=1
      continue
    fi
    [ "$in_setup" -eq 1 ] || continue
    # A new top-level key (no leading space, not a comment or list item) ends it.
    if [[ $line =~ ^[^[:space:]#-].*: ]]; then
      in_setup=0
      continue
    fi
    if [[ $line =~ ^[[:space:]]*-[[:space:]]+(.*)$ ]]; then
      item="${BASH_REMATCH[1]}"
      item="${item%"${item##*[![:space:]]}"}"            # rstrip trailing space
      if [[ $item =~ ^\"(.*)\"$ ]] || [[ $item =~ ^\'(.*)\'$ ]]; then
        item="${BASH_REMATCH[1]}"                          # strip wrapping quotes
      fi
      [ -n "$item" ] && printf '%s\n' "$item"
    fi
  done < "$1"
}

cd "$WORKSPACE" 2>/dev/null || {
  echo "bootstrap: workspace $WORKSPACE not found; nothing to do"
  exit 0
}

ran_install=0
ran_setup=0

# 1. Runtimes via mise, if the project declares them.
if [ -f "$WORKSPACE/.mise.toml" ] || [ -f "$WORKSPACE/.tool-versions" ]; then
  mise install
  mise reshim
  ran_install=1
fi

# 2. Setup commands from specs/toolset.yaml, in order, under the mise env.
if [ -f "$TOOLSET_YAML" ]; then
  while IFS= read -r cmd; do
    [ -n "$cmd" ] || continue
    echo "bootstrap: setup: $cmd"
    mise exec -- bash -c "$cmd"
    ran_setup=$((ran_setup + 1))
  done < <(parse_setup "$TOOLSET_YAML")
fi

# 3. Summary.
if [ "$ran_install" -eq 0 ] && [ "$ran_setup" -eq 0 ]; then
  echo "bootstrap: no toolset config found; nothing to do"
else
  echo "bootstrap: done (runtimes installed: $ran_install, setup commands run: $ran_setup)"
fi
```

- [ ] **Step 4: Run the harness to verify it PASSES**

```bash
cd "$(git rev-parse --show-toplevel)"
bash agent-worker/tests/bootstrap_local_test.sh
```
Expected: four `PASS: …` lines and `ALL PASS`.

- [ ] **Step 5: Syntax-check and commit**

```bash
cd "$(git rev-parse --show-toplevel)"
bash -n agent-worker/bootstrap.sh && echo "syntax ok"
chmod +x agent-worker/bootstrap.sh agent-worker/tests/bootstrap_local_test.sh
git add agent-worker/bootstrap.sh agent-worker/tests/bootstrap_local_test.sh
git commit -m "feat(agent-worker): toolset bootstrap orchestrator (mise + setup)"
```

---

### Task 2: Cross-role `bootstrap` skill

**Files:**
- Create: `agent-worker/config/skills/common/bootstrap/SKILL.md`

**Interfaces:**
- Produces: a skill directory under `config/skills/common/`. The entrypoint (Task 3) copies the **contents** of `config/skills/common/` into `~/.claude/skills/`, yielding `~/.claude/skills/bootstrap/SKILL.md`. Frontmatter `name: bootstrap` (the entrypoint harness and README grep for this path).

- [ ] **Step 1: Create `agent-worker/config/skills/common/bootstrap/SKILL.md`**

```markdown
---
name: bootstrap
description: Use when the project's toolset changes or a build/test fails on a missing tool - re-runs the deterministic toolset bootstrap (mise install + reshim, then specs/toolset.yaml setup commands).
---

# Bootstrap the project toolset

Run this when:
- you edited the project's `.mise.toml` / `.tool-versions` or `specs/toolset.yaml`, or
- a build or test just failed because a runtime or dependency was missing.

Steps:
1. From the project root, run `bootstrap.sh`.
2. It installs the pinned runtimes via mise, puts them on `PATH` (`mise reshim`),
   then runs the ordered `setup:` commands from `specs/toolset.yaml`.
3. If a `setup:` command fails, bootstrap stops at it — fix that command (or the
   tool versions) and re-run.

This is idempotent: re-running with nothing changed is a fast no-op.
```

- [ ] **Step 2: Verify the file and frontmatter**

```bash
cd "$(git rev-parse --show-toplevel)"
test -f agent-worker/config/skills/common/bootstrap/SKILL.md && echo "file ok"
grep -q "name: bootstrap" agent-worker/config/skills/common/bootstrap/SKILL.md && echo "frontmatter ok"
```
Expected: `file ok`, `frontmatter ok`.

- [ ] **Step 3: Commit**

```bash
git add agent-worker/config/skills/common/
git commit -m "feat(agent-worker): add cross-role bootstrap skill"
```

---

### Task 3: Entrypoint integration (common skills + gated bootstrap)

**Files:**
- Modify: `agent-worker/entrypoint.sh` (full replacement below)
- Create (committed regression test): `agent-worker/tests/entrypoint_local_test.sh`

**Interfaces:**
- Consumes: `config/skills/common/` (Task 2); `bootstrap.sh` on `PATH` (Task 1, baked in Task 4).
- Produces: an entrypoint that, after composing `CLAUDE.md` and registering the memory MCP server, (a) copies `config/skills/common/` into `~/.claude/skills/` for every role, and (b) when `BOOTSTRAP` (default `auto`) is not `skip` and `bootstrap.sh` is on `PATH`, runs it and **warns-and-continues** on failure. Reads new env `BOOTSTRAP`; exports `WORKSPACE` so `bootstrap.sh` inherits it. All Phase 2 behavior (role validation, CLAUDE.md composition, role skills, MCP registration, `cd $WORKSPACE`, `exec "$@"`) is preserved.

- [ ] **Step 1: Write `agent-worker/tests/entrypoint_local_test.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
ENTRY="$REPO/agent-worker/entrypoint.sh"

WORK="$(mktemp -d)"
HOME_DIR="$WORK/home"
BIN="$WORK/bin"
MARKER="$WORK/bootstrap-ran"
mkdir -p "$HOME_DIR" "$BIN"

cat > "$BIN/claude" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$BIN/claude"

# Stub bootstrap.sh: touch a marker so we can assert whether it ran.
cat > "$BIN/bootstrap.sh" <<EOF
#!/usr/bin/env bash
touch "$MARKER"
EOF
chmod +x "$BIN/bootstrap.sh"

run() {  # run <role> <bootstrap-mode> <cmd...>
  local role="$1" mode="$2"; shift 2
  env -i PATH="$BIN:/usr/bin:/bin" HOME="$HOME_DIR" \
    CONFIG_DIR="$REPO/agent-worker/config" WORKSPACE="$WORK" \
    AGENT_ROLE="$role" BOOTSTRAP="$mode" bash "$ENTRY" "$@"
}

# Phase 2 still intact + common skill enabled; BOOTSTRAP=skip -> no bootstrap.
rm -rf "$HOME_DIR/.claude"; rm -f "$MARKER"
run developer skip true
grep -q "Engineering DNA" "$HOME_DIR/.claude/CLAUDE.md"
grep -q "Role: Developer" "$HOME_DIR/.claude/CLAUDE.md"
test -f "$HOME_DIR/.claude/skills/scaffold/SKILL.md"        # role skill
test -f "$HOME_DIR/.claude/skills/bootstrap/SKILL.md"       # common skill
test ! -f "$MARKER"
echo "PASS: phase-2 intact + common skill; BOOTSTRAP=skip does not run bootstrap"

# BOOTSTRAP=auto -> bootstrap runs.
rm -f "$MARKER"
run reviewer auto true
test -f "$MARKER"
echo "PASS: BOOTSTRAP=auto runs bootstrap"

rm -rf "$WORK"
echo "ALL PASS"
```

- [ ] **Step 2: Run the harness to verify it FAILS**

```bash
cd "$(git rev-parse --show-toplevel)"
bash agent-worker/tests/entrypoint_local_test.sh
```
Expected: FAIL — the current entrypoint neither copies `skills/common/` (so `~/.claude/skills/bootstrap/SKILL.md` is missing) nor runs bootstrap; the first assertion to fail is `test -f …/skills/bootstrap/SKILL.md`.

- [ ] **Step 3: Replace `agent-worker/entrypoint.sh`**

Replace the entire file with:

```bash
#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${CONFIG_DIR:-/opt/agent-worker/config}"
WORKSPACE="${WORKSPACE:-/workspace}"
MEMORY_PYTHON="${MEMORY_PYTHON:-/opt/memory/.venv/bin/python}"
AGENT_ROLE="${AGENT_ROLE:-developer}"
HOME_DIR="${HOME:-/home/agent}"
BOOTSTRAP="${BOOTSTRAP:-auto}"
export WORKSPACE

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

# 3. Enable skills: the role's scoped skills + cross-role common skills.
if [ -d "$CONFIG_DIR/skills/$AGENT_ROLE" ]; then
  cp -r "$CONFIG_DIR/skills/$AGENT_ROLE/." "$HOME_DIR/.claude/skills/"
fi
if [ -d "$CONFIG_DIR/skills/common" ]; then
  cp -r "$CONFIG_DIR/skills/common/." "$HOME_DIR/.claude/skills/"
fi

# 4. Register the memory MCP server (stdio subprocess; inherits DB/embeddings env).
if command -v claude >/dev/null 2>&1; then
  claude mcp remove memory --scope user >/dev/null 2>&1 || true
  claude mcp add memory --scope user -- "$MEMORY_PYTHON" -m memory.mcp_server
fi

# 5. Bootstrap the project's toolset (warn and continue on failure).
cd "$WORKSPACE" 2>/dev/null || true
if [ "$BOOTSTRAP" != "skip" ] && command -v bootstrap.sh >/dev/null 2>&1; then
  if ! bootstrap.sh; then
    echo "entrypoint: bootstrap failed; continuing (re-run the bootstrap skill once fixed)" >&2
  fi
fi

# 6. Run the requested command (default: bash).
exec "$@"
```

- [ ] **Step 4: Run the harness to verify it PASSES**

```bash
cd "$(git rev-parse --show-toplevel)"
bash agent-worker/tests/entrypoint_local_test.sh
```
Expected: both `PASS: …` lines and `ALL PASS`.

- [ ] **Step 5: Syntax-check and commit**

```bash
cd "$(git rev-parse --show-toplevel)"
bash -n agent-worker/entrypoint.sh && echo "syntax ok"
chmod +x agent-worker/tests/entrypoint_local_test.sh
git add agent-worker/entrypoint.sh agent-worker/tests/entrypoint_local_test.sh
git commit -m "feat(agent-worker): entrypoint enables common skills + runs bootstrap"
```

---

### Task 4: Bake mise into the image

**Files:**
- Modify: `agent-worker/Dockerfile` (full replacement below)

**Interfaces:**
- Consumes: `agent-worker/bootstrap.sh` (Task 1), updated `entrypoint.sh` (Task 3), `config/skills/common/` (Task 2, already under `config/`).
- Produces: an image with `mise` at `/usr/local/bin/mise`; `bootstrap.sh` at `/usr/local/bin/bootstrap.sh` (on `PATH`, executable); env `MISE_DATA_DIR=/opt/mise/data` (created, owned by `agent`), `MISE_TRUSTED_CONFIG_PATHS=/workspace`, and `/opt/mise/data/shims` prepended to `PATH`.

- [ ] **Step 1: Replace `agent-worker/Dockerfile`**

Replace the entire file with (new lines: the `mise` install RUN, the `bootstrap.sh` COPY, the data-dir creation, and three `ENV` additions):

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

# mise: polyglot version manager (single static binary, arm64, non-root).
RUN curl -fsSL https://mise.run | MISE_INSTALL_PATH=/usr/local/bin/mise sh \
 && chmod +x /usr/local/bin/mise \
 && mise --version

# Claude Code CLI (the bundled reference MCP client).
RUN npm install -g @anthropic-ai/claude-code

# Memory MCP server: EDITABLE install so db.py resolves ../sql/001_schema.sql
# via Path(__file__).resolve().parents[2] / "sql".
COPY memory-service/pyproject.toml /opt/memory/pyproject.toml
COPY memory-service/uv.lock        /opt/memory/uv.lock
COPY memory-service/src            /opt/memory/src
COPY memory-service/sql            /opt/memory/sql
RUN cd /opt/memory && uv sync

# Baked role config, skills, entrypoint, and the toolset bootstrap script.
COPY agent-worker/config        /opt/agent-worker/config
COPY agent-worker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY agent-worker/bootstrap.sh  /usr/local/bin/bootstrap.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/bootstrap.sh

# Non-root user + mise data dir (volume-backed at runtime; owned by agent).
RUN useradd --create-home --shell /bin/bash agent \
 && mkdir -p /opt/mise/data \
 && chown -R agent:agent /opt/mise/data
USER agent
WORKDIR /workspace

ENV CONFIG_DIR=/opt/agent-worker/config \
    MEMORY_PYTHON=/opt/memory/.venv/bin/python \
    MISE_DATA_DIR=/opt/mise/data \
    MISE_TRUSTED_CONFIG_PATHS=/workspace \
    PATH=/opt/mise/data/shims:$PATH

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]
```

- [ ] **Step 2: Build the image (from the repo root)**

```bash
cd "$(git rev-parse --show-toplevel)"
docker build -f agent-worker/Dockerfile -t agent-worker:smoke .
```
Expected: a successful build; the `mise --version` line in the mise RUN prints a version during build.

- [ ] **Step 3: Smoke — mise present, shims on PATH, data dir writable**

```bash
docker run --rm agent-worker:smoke mise --version
docker run --rm agent-worker:smoke bash -c 'echo "$PATH" | grep -q /opt/mise/data/shims && echo "path ok"'
docker run --rm agent-worker:smoke bash -c 'touch /opt/mise/data/.probe && echo "data dir writable"'
```
Expected: a mise version string; `path ok`; `data dir writable`.

- [ ] **Step 4: Smoke — bootstrap is a clean no-op on an empty workspace**

```bash
docker run --rm agent-worker:smoke bootstrap.sh
```
Expected: `bootstrap: no toolset config found; nothing to do`, exit 0. (Plain `docker run` has an empty `/workspace`; the entrypoint runs first and — with no `claude` auth needed for registration and no toolset config — bootstrap no-ops, then `exec bootstrap.sh` runs it explicitly as the command, printing the same line.)

- [ ] **Step 5: Commit**

```bash
git add agent-worker/Dockerfile
git commit -m "feat(agent-worker): bake mise + bootstrap into the worker image"
```

---

### Task 5: Compose — mise cache volume + BOOTSTRAP passthrough

**Files:**
- Modify: `docker-compose.yml` (full replacement below)

**Interfaces:**
- Consumes: the image (Task 4).
- Produces: the `agent-worker` service gains `BOOTSTRAP: ${BOOTSTRAP:-auto}` in `environment` and a `mise-data:/opt/mise/data` volume mount; a top-level `mise-data` named volume is declared (merged with the included `pgdata`). Caches installed toolchains across `docker compose run` invocations.

- [ ] **Step 1: Replace the root `docker-compose.yml`**

```yaml
# Root compose: the whole system. Reuses the Phase 1 memory-service stack
# (db + embeddings + ingest worker) and adds the Phase 2/3 agent-worker.
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
      BOOTSTRAP: ${BOOTSTRAP:-auto}
    volumes:
      - ${PROJECT_DIR:-./}:/workspace
      - mise-data:/opt/mise/data
    stdin_open: true
    tty: true

volumes:
  mise-data:
```

- [ ] **Step 2: Validate the merged compose config**

```bash
cd "$(git rev-parse --show-toplevel)"
docker compose config >/dev/null && echo "compose config ok"
docker compose config --volumes | sort
docker compose config | grep -A1 'BOOTSTRAP'
```
Expected: `compose config ok`; the volumes list includes both `mise-data` and `pgdata`; the `BOOTSTRAP` env resolves to `auto`.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(agent-worker): mise cache volume + BOOTSTRAP passthrough"
```

---

### Task 6: End-to-end docker smoke + README

**Files:**
- Create: `agent-worker/tests/fixtures/bootstrap-project/.tool-versions`
- Create: `agent-worker/tests/fixtures/bootstrap-project/specs/toolset.yaml`
- Modify: `agent-worker/tests/smoke.sh` (insert check 6)
- Modify: `agent-worker/README.md` (add a Toolset bootstrap section)

**Interfaces:**
- Consumes: the image + compose (Tasks 4–5); the fixture project.
- Produces: a self-contained smoke check that bootstraps a pinned Node 18 runtime and runs a `setup:` command, asserting the pinned runtime is active and the setup effect is present. The fixture pins `node 18.20.4` (deliberately different from the image's system Node 20, proving the mise shim shadows it).

- [ ] **Step 1: Create the fixture `.tool-versions`**

`agent-worker/tests/fixtures/bootstrap-project/.tool-versions`:
```
node 18.20.4
```

- [ ] **Step 2: Create the fixture `specs/toolset.yaml`**

`agent-worker/tests/fixtures/bootstrap-project/specs/toolset.yaml`:
```yaml
# Bootstrap smoke fixture: write the active node version to a marker file.
setup:
  - node -e "require('fs').writeFileSync('setup-ran.txt', process.version)"
```

- [ ] **Step 3: Insert check 6 into `agent-worker/tests/smoke.sh`**

Find this block at the end of the file:
```bash
echo "== cleanup =="
docker compose down

echo "ALL SMOKE CHECKS PASSED"
```
and replace it with:
```bash
echo "== 6. toolset bootstrap e2e (pinned runtime + setup) =="
# Copy the fixture into /tmp (Docker Desktop shares /tmp; not /var/folders) so
# the setup marker lands outside the repo and the bind mount is allowed.
TMP_PROJ="$(mktemp -d /tmp/agent-bootstrap.XXXXXX)"
cp -R agent-worker/tests/fixtures/bootstrap-project/. "$TMP_PROJ/"
boot_out="$(PROJECT_DIR="$TMP_PROJ" docker compose run --rm --no-deps -e BOOTSTRAP=auto agent-worker \
  bash -c 'node --version && cat setup-ran.txt')"
echo "$boot_out" | grep -q 'v18.20.4'
echo "  pinned node active + setup ran ok"
rm -rf "$TMP_PROJ"

echo "== cleanup =="
docker compose down

echo "ALL SMOKE CHECKS PASSED"
```

- [ ] **Step 4: Run the full smoke suite**

```bash
cd "$(git rev-parse --show-toplevel)"
./agent-worker/tests/smoke.sh
```
Expected: checks 1–5 pass as before, then `== 6. toolset bootstrap e2e …` prints `  pinned node active + setup ran ok`, ending with `ALL SMOKE CHECKS PASSED`. (Check 6 needs egress for mise's first Node 18 download; subsequent runs hit the `mise-data` volume cache.)

- [ ] **Step 5: Add a Toolset bootstrap section to `agent-worker/README.md`**

Find this section header in the file:
```markdown
## Memory tools
```
and insert immediately before it:
```markdown
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

```

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add agent-worker/tests/fixtures/ agent-worker/tests/smoke.sh agent-worker/README.md
git commit -m "test(agent-worker): bootstrap e2e smoke + README"
```

---

## Self-Review notes (for the implementer)

- **Spec coverage:** §5.1 mise in the image → Task 4 (binary, `MISE_DATA_DIR`, shims on `PATH`, independent of system Node / memory venv); §5.2 toolset declaration → Task 6 fixture (`.tool-versions` + `specs/toolset.yaml`) and the `parse_setup` subset in Task 1; §5.3 bootstrap mechanism (cd, mise install+reshim, ordered setup, abort-on-fail, summary, no-op) → Task 1 (`bootstrap.sh`) verified by all four cases in `bootstrap_local_test.sh`; §5.4 entrypoint integration (common skills for every role + gated warn-and-continue bootstrap) → Task 3; §5.5 re-runnable skill → Task 2; §5.6 compose (`mise-data` volume + `BOOTSTRAP`) → Task 5. §7 testing: fast local harness → Task 1; entrypoint harness extension (skip + common skill) → Task 3; real docker smoke → Task 6.
- **Intentional simplification (spec §5.4):** the entrypoint gates only on `BOOTSTRAP != skip` and the presence of `bootstrap.sh`, relying on `bootstrap.sh`'s own no-op when `/workspace` has no toolset config, rather than duplicating the config-detection in the entrypoint. Net behavior matches the spec; DRY.
- **Determinism detail not in the spec:** `MISE_TRUSTED_CONFIG_PATHS=/workspace` (Task 4) auto-trusts the mounted project's mise config so `mise install` runs non-interactively — otherwise mise can refuse to load an untrusted config. Kept as an env var (no extra command), so `bootstrap.sh` stays thin and the stubbed harness needs no `trust` handling.
- **Type/name consistency:** `WORKSPACE` (default `/workspace`), `BOOTSTRAP` (default `auto`), `MISE_DATA_DIR=/opt/mise/data`, and the shims path `/opt/mise/data/shims` are identical across `bootstrap.sh` (Task 1), `entrypoint.sh` (Task 3), `Dockerfile` (Task 4), and `docker-compose.yml` (Task 5). The skill path `bootstrap/SKILL.md` (Task 2) is what Task 3's harness asserts. The mise stub contract (`install`/`reshim` logged; `exec -- <cmd>` passed through) in Task 1's harness matches `bootstrap.sh`'s real calls (`mise install`, `mise reshim`, `mise exec -- bash -c "$cmd"`).
- **Known interaction (spec §5.1 trade-off):** inside a mise-configured project the `node` shim shadows the system Node 20 that Claude Code runs under. Claude Code supports Node 18+, so this is low-risk; the Task 6 fixture pins Node 18.20.4 precisely to exercise (and prove) that shadowing.
- **macOS bind-mount gotcha:** Task 6 copies the fixture to `/tmp` (shared by Docker Desktop) rather than mounting from `/var/folders` (mktemp's default), which Docker Desktop does not share by default.
- **Placeholder scan:** none — every code/config step contains complete content and every command has an expected result.
```
