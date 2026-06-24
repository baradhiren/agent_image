# Agentic Dev Workspace Image — Design Spec

- **Status:** Draft for review
- **Date:** 2026-06-24
- **Author:** Hiren + Claude (Opus 4.8)
- **Source inputs:** [GOAL.md](../../../GOAL.md); Google whitepaper *"Spec-Driven Production Grade Development in the Age of Vibe Coding"* (Lee Boonstra, May 2026); brainstorming conversation with Claude Opus 4.8.

---

## 1. Problem statement

Working with coding agents across projects today suffers from three failures, all of which are
ultimately **one problem — the absence of a durable, retrievable project memory**:

1. **No shared memory across sessions.** Each session starts cold; accumulated understanding is lost.
2. **Context waste.** Project context is re-supplied (re-read, re-pasted) every session, burning token
   budget and latency before any work begins.
3. **Hallucination as the project grows.** As the codebase scales, historical knowledge fades and the
   agent reasons against stale or partial snapshots — the whitepaper's "context fragmentation."

The goal is a **reproducible Docker-based workspace** that gives agents a persistent memory layer they
*retrieve from* instead of *re-ingesting each session*, with human-in-the-loop concentrated at a few
high-leverage gates.

## 2. Goals / non-goals

**Goals**
- One **generic, runtime-agnostic** image. Projects are mounted in; any model (open or closed) connects
  via MCP.
- A **persistent knowledge layer** (code structure + doc/spec semantics + linkage) that survives sessions
  and stays fresh as code changes.
- **Role-based** agent configuration (developer / reviewer / design) scoping tools and skills.
- A design that **grows with project size** via defined escalation paths, not rewrites.

**Non-goals (v1)**
- Not an ephemeral untrusted-code-execution sandbox (the E2B/Firecracker "run untrusted output" pattern).
  This is a **persistent dev workspace**; continuity, not disposability, is the point.
- No baked-in language toolchains (see §6.1). No multi-agent orchestrator yet (interface stubbed only, §7).
- No team/multi-user features; single developer, local-first.

## 3. Constraints (locked decisions)

| Decision | Value | Rationale |
|---|---|---|
| Host | macOS Apple Silicon (arm64) | User's environment. No KVM → Firecracker/gVisor not native; Docker Desktop already runs containers inside a Linux VM, giving a VM boundary for free. |
| Trust level | Mostly own specs, low risk | Defends against *accidental* `rm -rf` / hallucinated side-effects, not malicious escape. |
| Agent runtime | Generic, MCP-based | Works with all open/closed models; MCP is the universal integration point. |
| Project delivery | Bind-mounted into container | Source of truth stays on host; container is disposable. |
| Retrieval need | Code-understanding **and** doc/spec RAG, equally | → pgvector as center of gravity, structure-first. |
| Embeddings | External service, **per-collection pluggable**; default self-hosted TEI | Model-free images; code/docs models chosen independently; code stays on the box by default. |
| Memory sync | **Auto on git commit** (incremental) | Memory tracks committed state cheaply; commits are the unit of durable change. |
| Orchestration | Workers now, interface stubbed | Ships a testable unit fast; orchestration layers on later without redesign. |
| Language toolchains | **Not baked in** | Image stays lean; project `specs/` declare the toolset, agent installs at bootstrap (§6.1). |

## 4. Architecture overview — two planes

The system separates strictly into two planes. This separation is the design's backbone: it lets the
**memory compound** while the **execution environment stays disposable**.

- **Persistent plane** (survives everything): the mounted git repo + `specs/` folder + the
  Postgres/pgvector knowledge base on a named Docker volume. The source of truth. Agents read/write here
  only through controlled interfaces, never by holding root over it.
- **Ephemeral plane** (per-session, disposable): the agent worker container — runtime, universal tools,
  role config, transient execution. Wiped and recreated freely; its loss costs nothing because the memory
  lives in the persistent plane.

```
host (macOS, Apple Silicon)
├── ./my-project/            ← bind-mounted (git repo + specs/)        [persistent, on host]
└── docker compose
    ├── memory-service        ← Postgres + pgvector + ingestion + MCP   [persistent: named volume]
    │     └── pgdata (named volume)
    └── agent-worker          ← universal tools + role config + MCP client [ephemeral]
          AGENT_ROLE=developer|reviewer|design
```

## 5. Component design

### 5.1 Agent Worker Image (the generic dev container)

**What it is:** a lean, runtime-agnostic container that mounts a project and runs whichever agent CLI you
invoke, all wired to the same memory + skills + role scoping.

**Choices:**
- **Base:** Debian-slim, `arm64`-native (Apple Silicon). Multi-arch-friendly for portability later.
- **Universal tooling only:** git + `gh`, `ripgrep`/`fd`, tree-sitter parsers, a headless browser (for the
  whitepaper's autonomous E2E / visual-verification pattern), the ingestion client, and MCP client config.
  **No language runtimes** — those come from the project spec at bootstrap (§6.1).
- **Bundled agent CLIs:** common open CLIs (e.g. Claude Code, Gemini CLI) plus MCP client config, so the
  *same* memory/skills/role apply regardless of which you launch. Model + API key injected at runtime via
  env → any open/closed model.
- **Role selection:** `AGENT_ROLE` env var picks the active config overlay + scoped tools/skills at start.

**Trade-offs:** A lean image means a per-project bootstrap cost (installing the toolset). Accepted now;
escalation is project-specific pre-baked images if that cost grows (§8). Bundling multiple agent CLIs adds
some size but keeps the "any model" promise real without per-runtime images.

**Resources:**
- Model Context Protocol — https://modelcontextprotocol.io
- tree-sitter — https://tree-sitter.github.io/tree-sitter/
- Docker multi-arch / `buildx` — https://docs.docker.com/build/building/multi-platform/

### 5.2 Memory Service (the heart)

**What it is:** the persistent knowledge layer agents retrieve from. One Postgres instance with three
cooperating layers, **structure-first, vectors-second** — because plain vector RAG *degrades* as a codebase
grows (it returns more irrelevant chunks faster), the opposite of what scaling requires. Efficiency at scale
comes from precision, not throughput.

**The three layers (one Postgres + pgvector):**
1. **Structure graph** — tables for files, symbols (functions/classes), and **resolved edges**
   (`symbol → symbol`, with `calls`/`imports`/`contains` kinds; unresolved/external calls keep a name and a
   resolution status). Answers "what calls `payment.process()` and what breaks if I change it" — a query
   that gets *more* valuable as the code grows. Resolved edges (not name-based) are what make `impact_of`
   precise and make the closure step below correct.
2. **Semantic layer** — pgvector embeddings of code chunks **and** doc/spec chunks in the same DB, so a
   single query returns a function *plus* the spec paragraph that defines it.
3. **Linkage layer** — explicit `spec ↔ symbol ↔ code` edges. The antidote to stale-snapshot hallucination:
   agents resolve against current, linked structure, not loose text. Linkage is invalidated on re-ingest and
   pruned of dangling references by the reconcile job (below), so it cannot silently drift.

**Ingestion pipeline (decoupled, drift-correct):**

```
git post-commit hook  →  enqueue {changed_files, commit_sha}        [fast, returns immediately]

worker drains the queue:
  parse (tree-sitter)                  # AST per changed file
  → chunk on AST nodes                 # function/class boundaries + file>class>fn breadcrumb + docstring;
                                       #   oversized functions split on inner-block boundaries, never mid-statement
  → diff by chunk content-hash         # skip chunks whose normalized semantics did not change
  → embed (batched, local)             # only new/changed chunks — the slow, costly step runs minimally
  → upsert: structure + vectors + linkage
  → recompute reverse-dependency edges # re-resolve edges whose target names changed in this file (closure)

periodic reconcile job  →  full re-scan, re-resolve all edges, prune dangling spec links   [drift safety net]
```

Four properties this buys, each addressing a specific failure:
- **Closure, not just changed files.** A changed signature invalidates *dependents whose own bytes did not
  change*. The worker re-resolves every edge whose target name changed in the ingested file — a cheap query
  against the structure graph — so the index does not drift as the codebase grows more interconnected.
- **Semantic-boundary chunks.** AST-node boundaries (plus structural breadcrumb) mean a function retrieves
  as one idea, not two half-meanings. This does more for retrieval precision than any embedding-model upgrade.
- **Chunk-level hashing.** Reformatting or an unrelated rename leaves a function's semantics untouched;
  per-chunk content hashes skip re-embedding it. The difference between a 2-second hook and one you dread.
- **Enqueue, don't embed, in the hook.** The hook does one fast thing (write to the queue) and returns; a
  failed embedding can never wedge a commit, and the worker gets free batching and a retry boundary. The
  reconcile job catches anything the incremental path missed (`--no-verify` commits, rebases, cross-branch
  merges).

The queue is a **plain Postgres table** (`ingest_queue`) drained by a worker process — same decoupling,
batching, and retry boundary as a message broker, with zero added infrastructure for a single-dev setup.

**Embeddings (external, model-free images):** the embedding **model runs as a separate service**, never
baked into the DB or worker images. A pluggable `EmbeddingProvider` is selected **per collection** — the
user can choose a different model for code than for docs (e.g. a code-aware model for code, a general model
for docs), local or hosted. The default backend is **self-hosted TEI** (Hugging Face Text Embeddings
Inference) so the secure "code never leaves the box" property holds out of the box; a local in-process
`fastembed` provider is retained as the offline/test default. Because a pgvector column's dimension is
fixed, each collection (`code`, `doc`) has its **own configured dimension**, and the `(collection →
provider, model, dim)` triple is recorded in an `embedding_config` table. On ingest, a mismatch against the
recorded config is **refused** (not silently mixed): swapping a model requires a reconcile/re-embed. This
makes the dimension constraint explicit and safe rather than a silent corruption footgun.

**MCP surface (what agents call instead of re-reading the repo):**
`search_code`, `search_docs`, `get_symbol`, `impact_of(symbol)`, `spec_for(symbol)`, `add_knowledge`.
This single interface is what directly resolves all three GOAL problems: shared memory (persists in the
volume), context efficiency (retrieve, don't re-dump), anti-hallucination (linked, fresh structure).

**Trade-offs:** pgvector is excellent to ~10–50M vectors; beyond that a single repo may need a swap to
Qdrant or a dedicated graph store (§8) — done **per project, behind the same MCP interface**, without
touching the rest. One Postgres (one backup, no sync pipeline) beats multiple stores you keep in sync,
which would reintroduce the very context fragmentation we're avoiding.

**Resources:**
- pgvector — https://github.com/pgvector/pgvector
- Postgres recursive CTEs — https://www.postgresql.org/docs/current/queries-with.html
- Qdrant (escalation) — https://qdrant.tech/documentation/
- LanceDB (alt. local store) — https://lancedb.github.io/lancedb/
- Retrieval-at-scale context: whitepaper §"Tier 3 at Full Scale: Graph-Native Code Understanding"

### 5.3 Instruction & config hierarchy (how agents get "direction")

Mirrors the whitepaper's layered-instruction model, translated to a tool-agnostic setup:

- **`AGENTS.md`** (cross-tool base, workspace root): engineering DNA — Spec-Driven/Behavior-Driven
  discipline, "propose folder structure + tech stack before coding," "fix root cause only / no drive-by
  cleanups," pin library versions, context-hygiene rules.
- **Role overlays:** `developer.md` / `reviewer.md` / `design.md` — scope each role's tools and skills.
- **`specs/` convention** (in the mounted project): Markdown narrative + **flat YAML** for deeply-nested
  config/schemas + **BDD/Gherkin** scenarios (`Scenario / Given / When / Then`). The whitepaper's format
  findings: Markdown anchors attention; YAML wins for nesting depth > 3; both beat heavy JSON on the
  "format tax."
- **Baked role-scoped skills:** reviewer → a `code-check` skill (the paper's security+logic review);
  developer → scaffold / feature / bugfix skills; design → UI skills.

**Trade-offs:** `AGENTS.md` (cross-tool) over a single-runtime file keeps the "any model" promise; the cost
is maintaining one shared file rather than leaning on one vendor's native format.

**Resources:**
- Gherkin reference — https://cucumber.io/docs/gherkin/reference
- `AGENTS.md` convention — https://agents.md
- Whitepaper §"Where do the instructions live?" and §"A good specification"

### 5.4 Safety & governance (right-sized: single-dev, mostly-trusted)

The real failure mode here is *accidental* damage and *confident-but-wrong* compounding changes — not
malicious escape. Cheap, high-leverage controls:

- **Filesystem:** project-only bind mount (**never** the home dir); non-root container user.
- **Network:** **egress allowlist, default-deny** — the single best defense against the
  prompt-injection → exfiltration chain.
- **Lifecycle:** TTL teardown for the *ephemeral* plane only; the persistent plane is never auto-destroyed.
- **Concentrated HITL gates** (to avoid the approval-fatigue burnout the paper cites): spec sign-off before
  generation; a checkpoint before anything irreversible (write to `main`, schema change, external send).
  Everything else runs free inside the sandbox.
- **`policies.yaml`** — lightweight **structural gating** (role/env → allowed/blocked tools). Semantic
  gating (LLM-as-referee for PII/intent) is deferred to an escalation (§8).

**Trade-offs:** Concentrated gates accept that low-risk in-sandbox actions run unsupervised, in exchange for
the developer actually paying attention at the moments that matter.

**Resources:**
- Whitepaper §"Zero-Trust Development", §"Human-in-the-Loop", §"Policy Server"
- Apple `container` / Containerization (isolation escalation) — https://github.com/apple/container
- Docker rootless mode — https://docs.docker.com/engine/security/rootless/

### 5.5 Wiring

A **`docker-compose.yml`** ties the worker(s) and the memory service together:
- **named volume** `pgdata` → Postgres persistence across sessions (this *is* "shared memory across
  sessions").
- **bind mount** → the active project (git repo + `specs/`).
- a compose network so workers reach the memory service's MCP endpoint.

## 6. Toolset bootstrap-from-spec

Because language toolchains are **not** baked in (§3), the worker performs a **bootstrap step** before
working:

1. On start, the agent reads the mounted project's `specs/` for a declared toolset (languages, runtimes,
   versions, package managers).
2. The agent installs that toolset into the ephemeral container.
3. Work proceeds.

**Rationale:** keeps the image lean and project-agnostic; the spec is already the source of truth for the
stack, so the toolset declaration lives where it belongs.

**Trade-off / escalation:** if on-the-fly toolset fetching consumes too much time/resource, build
**project-specific images** with the toolset pre-baked (the lean generic image becomes the base layer).
Deferred until the cost is felt.

## 7. Orchestration stub

v1 ships **standalone role workers** (each container = one role, sharing the mounted project + memory). To
make a future multi-agent orchestrator drop in cleanly without redesign, define the **handoff interface
now**:

- a `tasks` table in the shared Postgres (task graph, status, role assignment), and
- a role-assignment convention.

v1: the human drives hand-offs. v2: an orchestrator role (the whitepaper's Search → Impact → Tasks →
Coding pattern) consumes the same interface.

**Resources:** Whitepaper §"Tier 3 at Full Scale" (sub-agent pipeline); ADK multi-agent docs —
https://google.github.io/adk-docs/

## 8. Escalation paths (the "grows with project size" guarantee)

Each axis has a **default** and a **single defined escalation**, so growth is a config/swap, not a rewrite:

| Concern | Default | Escalate when… | Escalation |
|---|---|---|---|
| Retrieval | pgvector | repo > ~10–50M vectors | Qdrant / graph store per project, same MCP interface |
| Isolation | rootless container | untrusted deps appear | Apple `container` micro-VM per task |
| Embeddings | self-hosted TEI (model-free images) | want code-aware code retrieval / top quality | swap the per-collection model or point to a hosted API via config (+ re-embed) |
| Orchestration | stubbed `tasks` interface | need autonomous hand-off | full orchestrator role |
| Toolset | bootstrap-from-spec | fetch cost too high | project-specific pre-baked image |

---

# Part II — Usage Guidelines

1. **Spec-first.** Write the spec before generation: technical design (requirements, schemas, API
   contracts), BDD scenarios (Given/When/Then), and edge cases. Keep `specs/` and code in sync.

2. **Format for the model.** Markdown for narrative; flat YAML for nested config (depth > 3); pin every
   library version; keep specs lean.

3. **Use roles deliberately.** `developer` to build, `reviewer` to audit a diff, `design` for UI — one
   role per container.

4. **Retrieve, don't re-dump.** Point agents at the memory tools (`search_code`, `spec_for`, `impact_of`).
   Use the chat for orchestration and feedback loops, not context-pasting.

5. **Commit to refresh memory.** Commit at meaningful checkpoints; trigger a manual reindex for mid-session
   freshness.

6. **Concentrate HITL.** Sign off on specs; gate irreversible actions (write to `main`, schema change,
   external send). Let everything else run inside the sandbox.

7. **Bug-fixing discipline.** Reproduce first (failing test or `curl`), keep the test in the repo, fix only
   the root cause, defer unrelated cleanups/renames to a separate task.

8. **Stay on defaults; escalate on trigger.** Move off a default only when forced (retrieval slowness past
   tens of millions of vectors, untrusted deps, toolset-fetch cost).

---

## 9. Open questions / deferred

- Exact local embedding model(s) and chunking strategy for code vs docs — decided at implementation.
- Precise `tasks`-table schema for the orchestration stub — minimal in v1, expanded in v2.
- Whether to git-init this `agent_image` repo itself (currently not a git repo) for versioning the spec
  and build artifacts.

## 10. Key references

- Google whitepaper: *Spec-Driven Production Grade Development in the Age of Vibe Coding* (Lee Boonstra,
  May 2026) — provided as `Spec driven agentic coding.pdf`.
- Model Context Protocol — https://modelcontextprotocol.io
- pgvector — https://github.com/pgvector/pgvector
- tree-sitter — https://tree-sitter.github.io/tree-sitter/
- Gherkin / BDD — https://cucumber.io/docs/gherkin/reference
- Qdrant — https://qdrant.tech/documentation/
- Apple Containerization — https://github.com/apple/container
