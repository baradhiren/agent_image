# Phase 3 — Toolset Bootstrap-from-Spec Design Spec

- **Status:** Draft for review
- **Date:** 2026-06-25
- **Builds on:** Phase 1 + 1.5 (memory service) and Phase 2 (agent worker image), all merged to `main`.
- **Branch:** `feat/toolset-bootstrap`
- **Parent design:** [Agent Dev Workspace](2026-06-24-agent-dev-workspace-design.md) §6 (toolset bootstrap-from-spec).

---

## 1. Problem

The Phase 2 worker image deliberately bakes **no language toolchains** to stay lean. So today a `developer` agent on a mounted Node/Python/Go project cannot build or test until the toolchain is installed by hand. Phase 3 closes this: at session start the agent's declared toolchain is installed and ready, deterministically and with pinned versions.

## 2. Goals / non-goals

**Goals**
- Install the project's **declared, pinned** language runtimes at session start, before the agent works.
- Use a **maintained polyglot version manager** rather than hand-rolled per-language install logic.
- Make bootstrap **deterministic, idempotent, re-runnable**, and **cached** across container runs.

**Non-goals (deferred)**
- Governance (policies.yaml / egress allowlist / HITL); orchestration stub; build/test runners beyond a `setup:` command list; non-mise installers; per-language dependency caching tuning.

## 3. Constraints / decisions

| Decision | Value | Rationale |
|---|---|---|
| Installer | **mise** (single static binary) | Multi-language, pinned versions, arm64, non-root; avoids hand-rolling install logic. |
| Runtime declaration | project-root `.mise.toml` / `.tool-versions` | mise's native, auto-discovered config; honors "pin every version". |
| Setup declaration | optional `specs/toolset.yaml` (`setup:` list) | Thin schema we own for post-install dependency/setup commands. |
| Trigger | **auto at entrypoint** + re-runnable `bootstrap` skill | Workspace ready on start; agent can re-run after editing the toolset. |
| Failure mode | **warn and continue** (configurable `BOOTSTRAP=auto\|skip`) | A missing toolchain shouldn't trap the agent out of the session; it can debug/re-run. |
| Skill scope | new `config/skills/common/` copied for **every** role | bootstrap is cross-role; small extension to Phase 2's role-only skill mechanism. |
| Caching | named volume `mise-data:/opt/mise/data` | Toolchains persist across `docker compose run`; not reinstalled each session. |
| Memory reuse | no edits to `memory-service/` | Phase 3 only touches `agent-worker/` + root compose. |

## 4. Architecture

The worker image gains `mise`. On container start the Phase 2 entrypoint, after composing role config and registering the memory MCP server, runs a deterministic `bootstrap` step against the mounted `/workspace`. mise installs the project's pinned runtimes into a volume-backed data dir (cached across runs); then optional `specs/toolset.yaml` `setup:` commands install dependencies. The same `bootstrap` is exposed as a cross-role skill for re-runs. Nothing about the persistent memory plane changes.

```
docker compose run agent-worker
  └─ entrypoint.sh
       ├─ (Phase 2) compose ~/.claude/CLAUDE.md + enable skills + register memory MCP
       └─ (Phase 3) bootstrap  [if /workspace has a toolset config and BOOTSTRAP != skip]
            ├─ mise install      # pinned runtimes -> /opt/mise/data (named volume, cached)
            ├─ mise reshim       # shims on PATH
            └─ run specs/toolset.yaml: setup[]   # deps, under the mise env
```

## 5. Components

### 5.1 mise in the worker image (`agent-worker/Dockerfile`)
- Install `mise` as a static binary (arm64), available to the non-root `agent` user.
- `ENV MISE_DATA_DIR=/opt/mise/data` (created and owned by `agent`); add mise's shim dir to `PATH` so installed runtimes resolve in the entrypoint and the agent's (non-login) command execution. **Also add `/etc/profile.d/mise.sh` (`export PATH=/opt/mise/data/shims:$PATH`)** so login shells (`bash -l`, which re-source `/etc/profile` and would otherwise drop the `ENV PATH`) keep the shims too — without this, an interactive session falls back to system Node.
- mise is independent of: the system Node 20 that runs Claude Code, and the memory server's `/opt/memory/.venv`.

**Trade-off / known interaction:** mise's project `node` shim can shadow the system Node that Claude Code runs under; Claude Code supports Node 18+, so this is low-risk. We keep Claude Code on system Node and accept that, inside a mise-configured project, `node` resolves to the project's pinned version.

**Resources:** mise (https://mise.jdx.dev), `.tool-versions`/asdf format (https://mise.jdx.dev/configuration.html).

### 5.2 Toolset declaration
- **Runtimes** — the project's `.mise.toml` or `.tool-versions` at its root, pinned (e.g. `node 20.11.1`, `python 3.12.3`). mise auto-discovers.
- **Setup** — optional `specs/toolset.yaml`:
  ```yaml
  # specs/toolset.yaml
  setup:
    - pnpm install
    - uv sync
  ```
  An ordered list of shell commands run after runtimes install. Absent file → no setup step. (Only `setup:` in v1; `build:`/`test:` runners deferred.)

**Trade-off:** splitting runtimes (mise's native files) from setup (our `specs/toolset.yaml`) leverages mise's ecosystem for the hard part (version-pinned installs) while keeping a minimal schema we control for dependency commands. It does mean two files; acceptable because each has a single clear job.

### 5.3 Bootstrap mechanism (`agent-worker/bootstrap.sh`)
Deterministic, idempotent, re-runnable:
1. `cd "${WORKSPACE:-/workspace}"`.
2. If a mise config (`.mise.toml` or `.tool-versions`) is present → `mise install` then `mise reshim`.
3. If `specs/toolset.yaml` is present → run each `setup:` command in order, under the mise environment; abort on the first failing command (non-zero exit).
4. Print a one-line summary of what was installed/run.
5. No config present → no-op success.

**Trade-off:** a thin Bash orchestrator over mise (rather than a richer program) keeps it auditable and dependency-free; YAML parsing of `setup:` uses a minimal, well-defined subset (a top-level `setup:` list of strings).

### 5.4 Entrypoint integration (`agent-worker/entrypoint.sh`)
After the Phase 2 steps, and before `exec "$@"`: if `BOOTSTRAP` (default `auto`) is not `skip` and `/workspace` has a toolset config, run `bootstrap.sh`. On failure, print a clear warning and continue (do not exit). Also: extend the skill-enabling step to copy `config/skills/common/` (in addition to the role's skills) for every role.

**Trade-off:** warn-and-continue trades guaranteed readiness for not trapping the agent out of a session over a transient network failure; the agent re-runs via the skill.

### 5.5 Re-runnable skill (`agent-worker/config/skills/common/bootstrap/SKILL.md`)
A `bootstrap` skill, enabled for all roles, instructing the agent to run `bootstrap.sh` when the toolset config changes or a build fails on missing tools.

### 5.6 Compose (root `docker-compose.yml`)
Add a named volume `mise-data` mounted at `/opt/mise/data` on `agent-worker`, and pass `BOOTSTRAP` (default `auto`) through to the container.

## 6. Data flow

`docker compose run agent-worker` → entrypoint (Phase 2 config + MCP) → `bootstrap.sh` reads `/workspace` toolset config → `mise install` populates the cached `mise-data` volume → `mise reshim` puts runtimes on `PATH` → `specs/toolset.yaml` `setup:` commands install deps → agent session starts with the toolchain ready. Re-run any time via the `bootstrap` skill.

## 7. Testing

- **Fast local harness** (`bootstrap.sh` with a stubbed `mise` on `PATH`): asserts it (a) calls `mise install` + `mise reshim` when a `.tool-versions` is present, (b) runs `setup:` commands from `specs/toolset.yaml` in order, (c) aborts on a failing setup command, (d) no-ops cleanly when no config is present. Deterministic, no network.
- **Entrypoint harness extension:** with `BOOTSTRAP=skip`, bootstrap does not run; `config/skills/common/` is copied for an arbitrary role.
- **Real docker smoke:** a fixture project with a pinned `.tool-versions` (a small mise-installable runtime) + a `specs/toolset.yaml` `setup` step → run the worker → assert the pinned runtime version is active and the setup command's effect is present. (Needs egress; slower; this is the end-to-end proof.)

## 8. Out of scope / escalation paths

- **Governance** (egress allowlist must later permit mise's download endpoints; HITL; policy gating) — separate phase.
- **Orchestration stub** — separate phase.
- **Build/test runners** (`build:`/`test:` in `specs/toolset.yaml`) — additive schema extension.
- **Project-specific pre-baked images** (if on-the-fly install cost grows) — the parent design's escalation for bootstrap.
