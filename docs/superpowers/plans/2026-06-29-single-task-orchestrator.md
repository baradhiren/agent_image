# Single-Task Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `agentctl run --role developer path/to/task.md` — a host-side Python orchestrator that drives one task end-to-end through a bounded developer→reviewer→iterate loop across warm role containers, coordinating via a `tasks` table and tearing down cleanly (Q1 snapshot dump before `compose down`).

**Architecture:** A new host-side `orchestrator/` package owns the lifecycle and the bounded loop, written against thin seams (`exec_runner`, lifecycle `compose_*`/`snapshot_dump`) so the loop logic is unit-testable with no docker or DB. A `tasks` table + `TaskRepository` in the existing memory Postgres carry coordination state (snapshotted by Q1). A generic role runner (`memory/agent_runner.py`) drives a Claude Agent SDK session inside the matching warm container and writes its result to the `tasks` row.

**Tech Stack:** Python 3.12, psycopg 3, PostgreSQL 18 + pgvector 0.8.2, Claude Agent SDK (`claude-agent-sdk`), Docker Compose, uv, pytest.

## Global Constraints

- **Source spec:** [docs/superpowers/specs/2026-06-29-single-task-orchestrator-design.md](../specs/2026-06-29-single-task-orchestrator-design.md). This plan is **Q2 increment 1** (one task end-to-end). Decomposition, role inference, parallel worktree fan-out, the `design` role, per-role authoring, and mid-run resume are **out of scope**.
- **Builds on Q1:** the memory stack (db `pgvector/pgvector:0.8.2-pg18`, embeddings, one-shot `init` running `memory.startup`) and the Q1 entrypoints `python -m memory.startup /project` and `python -m memory.snapshot dump <dir>` already exist and are unchanged.
- **`tasks` table lives in the memory Postgres** (same DB as `files`/`spec_links`), so Q1 `pg_dump`/`pg_restore` snapshots it automatically. Add it to `memory-service/sql/001_schema.sql` and to the conftest truncation list.
- **Branch naming (verbatim rule):** git refnames cannot contain `:`. The branch is `feat/<task-id>-<tagline>`, where `<tagline>` is the task file's first H1 (`# Title`) kebab-cased, sanitized to `[a-z0-9-]`, and length-capped at 40 chars; fall back to the task filename stem if there is no H1.
- **Round cap default = 2.** On `needs_changes` at the cap, the terminal task status is `needs_changes` (a normal outcome, NOT `failed`).
- **Never tear down lossy:** the orchestrator always attempts the Q1 snapshot `dump` before `compose down` — including after a runner failure. If `dump` fails (non-zero), it does **not** `compose down`; it warns loudly and leaves the stack up.
- **Agent driver:** the in-container session uses the **Claude Agent SDK** (`from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock`) with `permission_mode="bypassPermissions"`, `cwd="/workspace"`, `system_prompt={"type": "preset", "preset": "claude_code"}` (so the role's composed `~/.claude/CLAUDE.md`, scoped skills, and registered memory MCP apply). NOT headless `claude -p`.
- **Reviewer verdict contract:** the reviewer agent ends its output with a line `VERDICT: approved` or `VERDICT: needs_changes`; the runner parses the last such line. Absent/unparseable → default `needs_changes` (safe).
- **SDK import is lazy:** `memory/agent_runner.py` imports `claude_agent_sdk` only inside the function that runs a session, so the module imports (and unit-tests) on hosts without the SDK, and the memory-service image stays SDK-free. The SDK is added only to the agent-worker image.
- **Settings/DB source of truth:** [memory-service/src/memory/config.py](../../../memory-service/src/memory/config.py) (`Settings.from_env`, `Settings.database_url`) and [memory-service/src/memory/db.py](../../../memory-service/src/memory/db.py) (`connect`, `apply_schema`).

### Test prerequisites

- pg18 server running: from `memory-service/`, `docker compose up -d db` (localhost:5432 = default `DATABASE_URL`).
- DB-backed tests (`TaskRepository`, runner) run from `memory-service/`: `uv run pytest ...`.
- Orchestrator tests run from `orchestrator/`: `uv run pytest ...`. They fake `subprocess`/seams and need neither docker nor the DB.
- Tasks that touch Q1 `dump`/`restore` need `pg_dump`/`pg_restore` major 18 on PATH (`/opt/homebrew/opt/postgresql@18/bin`).

---

### Task 1: `tasks` table + `TaskRepository`

The coordination table in the memory Postgres and its repository, mirroring the existing `Repository` pattern. Includes a guard that `tasks` rows survive the Q1 snapshot round-trip.

**Files:**
- Modify: `memory-service/sql/001_schema.sql` (add the `tasks` table)
- Create: `memory-service/src/memory/tasks.py`
- Modify: `memory-service/tests/conftest.py` (add `tasks` to the truncation list)
- Test: `memory-service/tests/test_tasks_repository.py`
- Test: `memory-service/tests/test_snapshot_restore.py` (add one round-trip test for `tasks`)

> **Existing-code change.** `001_schema.sql` and `conftest.py` change; documented in Task 10.

**Interfaces:**
- Consumes: `memory.db.connect`, `memory.config.Settings`, `memory.snapshot.dump`/`restore` (Q1).
- Produces — `memory.tasks.TaskRepository(conn)`:
  - `create(spec_ref: str, title: str, assignee_role: str) -> int`
  - `set_branch(task_id: int, branch: str) -> None`
  - `set_round(task_id: int, round: int) -> None`
  - `record_developer_result(task_id: int, summary: str, artifacts: list[str]) -> None`
  - `record_review(task_id: int, review_status: str, review_notes: str) -> None`
  - `set_status(task_id: int, status: str) -> None`
  - `get(task_id: int) -> dict | None` (keys: `id, spec_ref, title, assignee_role, branch, status, round, review_status, review_notes, summary, artifacts`)

- [ ] **Step 1: Add the `tasks` table to the schema**

Append to `memory-service/sql/001_schema.sql` (after the `embedding_config` table, before the indexes):

```sql
CREATE TABLE IF NOT EXISTS tasks (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_ref      TEXT NOT NULL,
    title         TEXT NOT NULL,
    assignee_role TEXT NOT NULL,
    branch        TEXT,
    status        TEXT NOT NULL DEFAULT 'in_progress',
    round         INT  NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'pending',
    review_notes  TEXT,
    summary       TEXT,
    artifacts     JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Add `tasks` to the conftest truncation list**

In `memory-service/tests/conftest.py`, change the `TABLES` list:

```python
TABLES = [
    "files", "symbols", "edges", "code_chunks", "doc_chunks",
    "spec_links", "ingest_queue", "embedding_config", "tasks",
]
```

- [ ] **Step 3: Write the failing tests**

```python
# memory-service/tests/test_tasks_repository.py
from memory.tasks import TaskRepository


def test_create_returns_id_and_defaults(conn):
    repo = TaskRepository(conn)
    tid = repo.create(spec_ref="tasks/x.md", title="Add CSV export", assignee_role="developer")
    row = repo.get(tid)
    assert row["id"] == tid
    assert row["spec_ref"] == "tasks/x.md"
    assert row["title"] == "Add CSV export"
    assert row["assignee_role"] == "developer"
    assert row["status"] == "in_progress"
    assert row["round"] == 0
    assert row["review_status"] == "pending"
    assert row["branch"] is None
    assert row["artifacts"] == []


def test_set_branch_round_status(conn):
    repo = TaskRepository(conn)
    tid = repo.create("s.md", "t", "developer")
    repo.set_branch(tid, "feat/1-t")
    repo.set_round(tid, 2)
    repo.set_status(tid, "approved")
    row = repo.get(tid)
    assert row["branch"] == "feat/1-t"
    assert row["round"] == 2
    assert row["status"] == "approved"


def test_record_developer_result_and_review(conn):
    repo = TaskRepository(conn)
    tid = repo.create("s.md", "t", "developer")
    repo.record_developer_result(tid, summary="did the thing", artifacts=["abc123", "def456"])
    repo.record_review(tid, review_status="needs_changes", review_notes="fix the edge case")
    row = repo.get(tid)
    assert row["summary"] == "did the thing"
    assert row["artifacts"] == ["abc123", "def456"]
    assert row["review_status"] == "needs_changes"
    assert row["review_notes"] == "fix the edge case"


def test_get_unknown_returns_none(conn):
    assert TaskRepository(conn).get(999999) is None
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `cd memory-service && docker compose up -d db && uv run pytest tests/test_tasks_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.tasks'`

- [ ] **Step 5: Write the implementation**

```python
# memory-service/src/memory/tasks.py
import json

import psycopg

_COLUMNS = (
    "id, spec_ref, title, assignee_role, branch, status, round, "
    "review_status, review_notes, summary, artifacts"
)


class TaskRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def create(self, spec_ref: str, title: str, assignee_role: str) -> int:
        return self._conn.execute(
            "INSERT INTO tasks (spec_ref, title, assignee_role) VALUES (%s, %s, %s) "
            "RETURNING id",
            (spec_ref, title, assignee_role),
        ).fetchone()[0]

    def set_branch(self, task_id: int, branch: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET branch = %s, updated_at = now() WHERE id = %s",
            (branch, task_id),
        )

    def set_round(self, task_id: int, round: int) -> None:
        self._conn.execute(
            "UPDATE tasks SET round = %s, updated_at = now() WHERE id = %s",
            (round, task_id),
        )

    def record_developer_result(self, task_id: int, summary: str, artifacts: list[str]) -> None:
        self._conn.execute(
            "UPDATE tasks SET summary = %s, artifacts = %s, updated_at = now() WHERE id = %s",
            (summary, json.dumps(artifacts), task_id),
        )

    def record_review(self, task_id: int, review_status: str, review_notes: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET review_status = %s, review_notes = %s, updated_at = now() "
            "WHERE id = %s",
            (review_status, review_notes, task_id),
        )

    def set_status(self, task_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = %s, updated_at = now() WHERE id = %s",
            (status, task_id),
        )

    def get(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if row is None:
            return None
        keys = [c.strip() for c in _COLUMNS.split(",")]
        return dict(zip(keys, row))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd memory-service && uv run pytest tests/test_tasks_repository.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: Add the snapshot round-trip guard for `tasks`**

Add to `memory-service/tests/test_snapshot_restore.py`:

```python
def test_roundtrip_preserves_tasks(tmp_path):
    from memory.tasks import TaskRepository
    settings = Settings.from_env()
    conn = connect(settings)
    reset_db(conn)
    apply_schema(conn)
    tid = TaskRepository(conn).create("tasks/x.md", "Add export", "developer")
    conn.close()

    snapshot.dump(str(tmp_path), settings)
    conn = connect(settings); reset_db(conn); conn.close()
    assert snapshot.restore(str(tmp_path), settings) is True

    conn = connect(settings)
    row = TaskRepository(conn).get(tid)
    conn.close()
    assert row is not None and row["title"] == "Add export"
```

- [ ] **Step 8: Run the round-trip test (pg18 client on PATH)**

Run: `cd memory-service && export PATH="/opt/homebrew/opt/postgresql@18/bin:$PATH" && uv run pytest tests/test_snapshot_restore.py tests/test_tasks_repository.py -v`
Expected: PASS (all pass; `tasks` rows survive dump→restore)

- [ ] **Step 9: Commit**

```bash
git add memory-service/sql/001_schema.sql memory-service/src/memory/tasks.py memory-service/tests/conftest.py memory-service/tests/test_tasks_repository.py memory-service/tests/test_snapshot_restore.py
git commit -m "feat(tasks): add tasks table + TaskRepository (snapshotted by Q1)"
```

---

### Task 2: Orchestrator package scaffolding + branch-name derivation

Create the host-side `orchestrator/` package and the pure branch-naming functions.

**Files:**
- Create: `orchestrator/pyproject.toml`
- Create: `orchestrator/src/orchestrator/__init__.py` (empty)
- Create: `orchestrator/src/orchestrator/branch.py`
- Test: `orchestrator/tests/test_branch_name.py`

**Interfaces:**
- Produces:
  - `orchestrator.branch.derive_tagline(task_text: str, fallback: str) -> str`
  - `orchestrator.branch.branch_name(task_id: int, tagline: str) -> str` → `f"feat/{task_id}-{tagline}"`

- [ ] **Step 1: Create the package files**

`orchestrator/pyproject.toml`:

```toml
[project]
name = "orchestrator"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "memory-service",
    "psycopg[binary]>=3.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.3"]

[project.scripts]
agentctl = "orchestrator.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/orchestrator"]

[tool.uv.sources]
memory-service = { path = "../memory-service", editable = true }

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

Create empty `orchestrator/src/orchestrator/__init__.py`.

- [ ] **Step 2: Write the failing tests**

```python
# orchestrator/tests/test_branch_name.py
from orchestrator.branch import branch_name, derive_tagline


def test_tagline_from_h1():
    assert derive_tagline("# Add CSV export\n\nbody", fallback="x") == "add-csv-export"


def test_tagline_strips_punct_and_caps():
    assert derive_tagline("# Fix: the *Bug*!! (urgent)", fallback="x") == "fix-the-bug-urgent"


def test_tagline_falls_back_when_no_h1():
    assert derive_tagline("no heading here", fallback="my-task-file") == "my-task-file"


def test_tagline_length_capped_at_40():
    long = "# " + "word " * 30
    assert len(derive_tagline(long, fallback="x")) <= 40
    assert not derive_tagline(long, fallback="x").endswith("-")


def test_branch_name_format():
    assert branch_name(42, "add-csv-export") == "feat/42-add-csv-export"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd orchestrator && uv run pytest tests/test_branch_name.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.branch'`

- [ ] **Step 4: Write the implementation**

```python
# orchestrator/src/orchestrator/branch.py
import re

_MAX_TAGLINE = 40


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > _MAX_TAGLINE:
        s = s[:_MAX_TAGLINE].rstrip("-")
    return s


def derive_tagline(task_text: str, fallback: str) -> str:
    for line in task_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            slug = _slug(stripped[2:])
            if slug:
                return slug
    return _slug(fallback)


def branch_name(task_id: int, tagline: str) -> str:
    return f"feat/{task_id}-{tagline}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd orchestrator && uv run pytest tests/test_branch_name.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add orchestrator/pyproject.toml orchestrator/src/orchestrator/__init__.py orchestrator/src/orchestrator/branch.py orchestrator/tests/test_branch_name.py
git commit -m "feat(orchestrator): scaffold package + pure branch-name derivation"
```

---

### Task 3: Branch git operations

Real git operations against `/workspace`: read the base branch, create the task branch, summarize the diff. Tested against a throwaway git repo.

**Files:**
- Modify: `orchestrator/src/orchestrator/branch.py` (add git ops)
- Test: `orchestrator/tests/test_branch_git.py`

**Interfaces:**
- Consumes: `branch_name` (Task 2).
- Produces:
  - `current_branch(workspace: str) -> str`
  - `create_task_branch(workspace: str, branch: str, base: str) -> None`
  - `checkout(workspace: str, ref: str) -> None`
  - `diff_summary(workspace: str, base: str, branch: str) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# orchestrator/tests/test_branch_git.py
import subprocess

from orchestrator.branch import (
    checkout, create_task_branch, current_branch, diff_summary,
)


def _init_repo(path):
    def g(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    (path / "a.txt").write_text("one\n")
    g("add", "a.txt")
    g("commit", "-m", "init")
    return g


def test_current_branch(tmp_path):
    _init_repo(tmp_path)
    assert current_branch(str(tmp_path)) == "main"


def test_create_task_branch_switches(tmp_path):
    _init_repo(tmp_path)
    create_task_branch(str(tmp_path), "feat/1-x", base="main")
    assert current_branch(str(tmp_path)) == "feat/1-x"


def test_diff_summary_reports_changes(tmp_path):
    g = _init_repo(tmp_path)
    create_task_branch(str(tmp_path), "feat/1-x", base="main")
    (tmp_path / "b.txt").write_text("two\n")
    g("add", "b.txt")
    g("commit", "-m", "add b")
    out = diff_summary(str(tmp_path), base="main", branch="feat/1-x")
    assert "b.txt" in out


def test_checkout_restores_base(tmp_path):
    _init_repo(tmp_path)
    create_task_branch(str(tmp_path), "feat/1-x", base="main")
    assert current_branch(str(tmp_path)) == "feat/1-x"
    checkout(str(tmp_path), "main")
    assert current_branch(str(tmp_path)) == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd orchestrator && uv run pytest tests/test_branch_git.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_task_branch'`

- [ ] **Step 3: Write the implementation**

Add to `orchestrator/src/orchestrator/branch.py`:

```python
import subprocess


def _git(workspace: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", workspace, *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def current_branch(workspace: str) -> str:
    return _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")


def create_task_branch(workspace: str, branch: str, base: str) -> None:
    _git(workspace, "checkout", "-b", branch, base)


def checkout(workspace: str, ref: str) -> None:
    _git(workspace, "checkout", ref)


def diff_summary(workspace: str, base: str, branch: str) -> str:
    return _git(workspace, "diff", "--stat", f"{base}..{branch}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd orchestrator && uv run pytest tests/test_branch_git.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/orchestrator/branch.py orchestrator/tests/test_branch_git.py
git commit -m "feat(orchestrator): branch git operations (current/create/diff)"
```

---

### Task 4: Subprocess seams — `exec_runner` + lifecycle

Thin wrappers over `docker compose` that the loop is written against. They build commands + env; tests fake `subprocess.run` and assert the command/env, so no docker is needed.

**Files:**
- Create: `orchestrator/src/orchestrator/seams.py`
- Test: `orchestrator/tests/test_seams.py`

**Interfaces:**
- Produces:
  - `@dataclass RunnerResult: ok: bool; exit_code: int`
  - `exec_runner(role: str, task_id: int, round: int, *, compose_dir: str, project_dir: str) -> RunnerResult`
  - `compose_up(*, compose_dir: str, project_dir: str) -> None`
  - `compose_down(*, compose_dir: str, project_dir: str) -> None`
  - `snapshot_dump(*, compose_dir: str, project_dir: str) -> None` (raises `subprocess.CalledProcessError` on failure)

- [ ] **Step 1: Write the failing tests**

```python
# orchestrator/tests/test_seams.py
import subprocess

from orchestrator import seams


def _fake_run(record, returncode=0):
    def run(cmd, **kwargs):
        record.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode)
    return run


def test_exec_runner_builds_compose_exec(monkeypatch):
    rec = []
    monkeypatch.setattr(seams.subprocess, "run", _fake_run(rec, returncode=0))
    res = seams.exec_runner("developer", 7, 1, compose_dir="/img", project_dir="/proj")
    assert res.ok is True and res.exit_code == 0
    cmd, kwargs = rec[0]
    assert cmd[:4] == ["docker", "compose", "exec", "-T"]
    assert "developer" in cmd
    assert "--task-id" in cmd and "7" in cmd
    assert "--round" in cmd and "1" in cmd
    assert kwargs["cwd"] == "/img"
    assert kwargs["env"]["PROJECT_DIR"] == "/proj"


def test_exec_runner_nonzero_is_not_ok(monkeypatch):
    rec = []
    monkeypatch.setattr(seams.subprocess, "run", _fake_run(rec, returncode=2))
    res = seams.exec_runner("reviewer", 7, 2, compose_dir="/img", project_dir="/proj")
    assert res.ok is False and res.exit_code == 2


def test_compose_up_uses_wait(monkeypatch):
    rec = []
    monkeypatch.setattr(seams.subprocess, "run", _fake_run(rec))
    seams.compose_up(compose_dir="/img", project_dir="/proj")
    cmd, kwargs = rec[0]
    assert cmd[:3] == ["docker", "compose", "up"]
    assert "-d" in cmd and "--wait" in cmd
    assert kwargs["env"]["PROJECT_DIR"] == "/proj"


def test_snapshot_dump_raises_on_failure(monkeypatch):
    def run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(seams.subprocess, "run", run)
    try:
        seams.snapshot_dump(compose_dir="/img", project_dir="/proj")
        raised = False
    except subprocess.CalledProcessError:
        raised = True
    assert raised is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd orchestrator && uv run pytest tests/test_seams.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.seams'`

- [ ] **Step 3: Write the implementation**

```python
# orchestrator/src/orchestrator/seams.py
import os
import subprocess
from dataclasses import dataclass

RUNNER_PYTHON = "/opt/memory/.venv/bin/python"


@dataclass(frozen=True)
class RunnerResult:
    ok: bool
    exit_code: int


def _env(project_dir: str) -> dict:
    return {**os.environ, "PROJECT_DIR": project_dir}


def exec_runner(role: str, task_id: int, round: int, *, compose_dir: str,
                project_dir: str) -> RunnerResult:
    cmd = [
        "docker", "compose", "exec", "-T", role,
        RUNNER_PYTHON, "-m", "memory.agent_runner",
        "--role", role, "--task-id", str(task_id), "--round", str(round),
    ]
    proc = subprocess.run(cmd, cwd=compose_dir, env=_env(project_dir))
    return RunnerResult(ok=proc.returncode == 0, exit_code=proc.returncode)


def compose_up(*, compose_dir: str, project_dir: str) -> None:
    subprocess.run(
        ["docker", "compose", "up", "-d", "--wait"],
        cwd=compose_dir, env=_env(project_dir), check=True,
    )


def compose_down(*, compose_dir: str, project_dir: str) -> None:
    subprocess.run(
        ["docker", "compose", "down"],
        cwd=compose_dir, env=_env(project_dir), check=True,
    )


def snapshot_dump(*, compose_dir: str, project_dir: str) -> None:
    # Run the Q1 dump in a one-shot init container (it mounts /project read-write
    # and carries the pg18 client). Raises CalledProcessError on failure.
    subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps", "init",
         "uv", "run", "python", "-m", "memory.snapshot", "dump",
         "/project/.agent-memory"],
        cwd=compose_dir, env=_env(project_dir), check=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd orchestrator && uv run pytest tests/test_seams.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/orchestrator/seams.py orchestrator/tests/test_seams.py
git commit -m "feat(orchestrator): docker subprocess seams (exec_runner + lifecycle)"
```

---

### Task 5: The control loop (`run_task`)

The bounded developer→reviewer→iterate loop, written against injected collaborators so it is fully unit-testable with no docker or DB.

**Files:**
- Create: `orchestrator/src/orchestrator/loop.py`
- Test: `orchestrator/tests/test_loop.py`

**Interfaces:**
- Consumes: `TaskRepository` (Task 1, duck-typed), `branch_name` (Task 2), `RunnerResult` (Task 4).
- Produces:
  - `@dataclass TaskReport: task_id:int; branch:str; status:str; review_status:str; review_notes:str|None; rounds:int; summary:str|None; artifacts:list`
  - `run_task(task_file: str, role: str, *, repo, make_branch, run_developer, run_reviewer, cap: int = 2) -> TaskReport`
    - `make_branch: Callable[[str], None]` — creates the given branch name in the workspace.
    - `run_developer`/`run_reviewer: Callable[[int, int], RunnerResult]` — `(task_id, round)`.

- [ ] **Step 1: Write the failing tests**

```python
# orchestrator/tests/test_loop.py
from orchestrator.loop import run_task
from orchestrator.seams import RunnerResult


class FakeRepo:
    """In-memory stand-in for TaskRepository."""
    def __init__(self, review_sequence):
        self._review = list(review_sequence)  # e.g. ["needs_changes", "approved"]
        self.rows = {}
        self._next = 1
        self.calls = []

    def create(self, spec_ref, title, assignee_role):
        tid = self._next; self._next += 1
        self.rows[tid] = {"id": tid, "spec_ref": spec_ref, "title": title,
                          "assignee_role": assignee_role, "branch": None,
                          "status": "in_progress", "round": 0,
                          "review_status": "pending", "review_notes": None,
                          "summary": None, "artifacts": []}
        return tid

    def set_branch(self, tid, branch): self.rows[tid]["branch"] = branch
    def set_round(self, tid, r): self.rows[tid]["round"] = r
    def set_status(self, tid, s): self.rows[tid]["status"] = s; self.calls.append(("status", s))
    def record_review(self, tid, rs, rn):
        self.rows[tid]["review_status"] = rs; self.rows[tid]["review_notes"] = rn
    def record_developer_result(self, tid, summary, artifacts):
        self.rows[tid]["summary"] = summary; self.rows[tid]["artifacts"] = artifacts
    def get(self, tid): return self.rows.get(tid)


def _runner_ok(repo, role_writes):
    # returns a run_* callable that records a review verdict when role_writes set
    def run(task_id, round):
        if role_writes is not None:
            rs = role_writes.pop(0)
            repo.record_review(task_id, rs, f"notes-{rs}")
        return RunnerResult(ok=True, exit_code=0)
    return run


def _write_task(tmp_path):
    f = tmp_path / "t.md"; f.write_text("# Add export\n\nbody"); return str(f)


def test_approve_round_1(tmp_path):
    repo = FakeRepo([])
    verdicts = ["approved"]
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None,
                      run_developer=lambda t, r: RunnerResult(True, 0),
                      run_reviewer=_runner_ok(repo, verdicts), cap=2)
    assert report.status == "approved"
    assert report.review_status == "approved"
    assert report.rounds == 1
    assert report.branch == "feat/1-add-export"


def test_needs_changes_then_approve(tmp_path):
    repo = FakeRepo([])
    verdicts = ["needs_changes", "approved"]
    seen_rounds = []
    def dev(t, r): seen_rounds.append(r); return RunnerResult(True, 0)
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None, run_developer=dev,
                      run_reviewer=_runner_ok(repo, verdicts), cap=2)
    assert report.status == "approved"
    assert report.rounds == 2
    assert seen_rounds == [1, 2]  # developer re-run on round 2


def test_cap_reached_is_needs_changes_not_failed(tmp_path):
    repo = FakeRepo([])
    verdicts = ["needs_changes", "needs_changes"]
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None,
                      run_developer=lambda t, r: RunnerResult(True, 0),
                      run_reviewer=_runner_ok(repo, verdicts), cap=2)
    assert report.status == "needs_changes"
    assert report.rounds == 2


def test_developer_failure_marks_failed(tmp_path):
    repo = FakeRepo([])
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None,
                      run_developer=lambda t, r: RunnerResult(ok=False, exit_code=1),
                      run_reviewer=lambda t, r: RunnerResult(True, 0), cap=2)
    assert report.status == "failed"
    assert report.rounds == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd orchestrator && uv run pytest tests/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.loop'`

- [ ] **Step 3: Write the implementation**

```python
# orchestrator/src/orchestrator/loop.py
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orchestrator.branch import branch_name, derive_tagline
from orchestrator.seams import RunnerResult


@dataclass
class TaskReport:
    task_id: int
    branch: str
    status: str
    review_status: str
    review_notes: str | None
    rounds: int
    summary: str | None
    artifacts: list


def _report(repo, task_id: int, rounds: int) -> TaskReport:
    row = repo.get(task_id)
    return TaskReport(
        task_id=task_id, branch=row["branch"], status=row["status"],
        review_status=row["review_status"], review_notes=row["review_notes"],
        rounds=rounds, summary=row["summary"], artifacts=row["artifacts"],
    )


def run_task(task_file: str, role: str, *, repo,
             make_branch: Callable[[str], None],
             run_developer: Callable[[int, int], RunnerResult],
             run_reviewer: Callable[[int, int], RunnerResult],
             cap: int = 2) -> TaskReport:
    text = Path(task_file).read_text(encoding="utf-8")
    title_line = next((ln.strip()[2:] for ln in text.splitlines()
                       if ln.strip().startswith("# ")), Path(task_file).stem)
    tagline = derive_tagline(text, fallback=Path(task_file).stem)

    task_id = repo.create(spec_ref=task_file, title=title_line, assignee_role=role)
    branch = branch_name(task_id, tagline)
    make_branch(branch)
    repo.set_branch(task_id, branch)

    rounds = 0
    for round in range(1, cap + 1):
        rounds = round
        repo.set_round(task_id, round)

        dev = run_developer(task_id, round)
        if not dev.ok:
            repo.set_status(task_id, "failed")
            return _report(repo, task_id, rounds)

        rev = run_reviewer(task_id, round)
        if not rev.ok:
            repo.set_status(task_id, "failed")
            return _report(repo, task_id, rounds)

        if repo.get(task_id)["review_status"] == "approved":
            repo.set_status(task_id, "approved")
            return _report(repo, task_id, rounds)

    repo.set_status(task_id, "needs_changes")  # cap reached
    return _report(repo, task_id, rounds)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd orchestrator && uv run pytest tests/test_loop.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/orchestrator/loop.py orchestrator/tests/test_loop.py
git commit -m "feat(orchestrator): bounded developer-reviewer-iterate control loop"
```

---

### Task 6: The lifecycle bracket (`orchestrate`)

Wrap `run_task` with the compose lifecycle and the spec's error handling: always attempt `dump` before `down`; if `dump` fails do not `down`; if `run_task` raises, still attempt `dump`.

**Files:**
- Create: `orchestrator/src/orchestrator/orchestrate.py`
- Test: `orchestrator/tests/test_orchestrate.py`

**Interfaces:**
- Consumes: `compose_up`/`compose_down`/`snapshot_dump` (Task 4), `run_task` (Task 5).
- Produces:
  - `orchestrate(*, run_task_fn: Callable[[], TaskReport], up, down, dump) -> TaskReport`
    - `up`/`down`/`dump` are `Callable[[], None]` (already bound to `compose_dir`/`project_dir` by the caller).
    - On `dump` raising `subprocess.CalledProcessError`: prints a loud stderr warning and does NOT call `down`; re-raises.

- [ ] **Step 1: Write the failing tests**

```python
# orchestrator/tests/test_orchestrate.py
import subprocess

import pytest

from orchestrator.orchestrate import orchestrate
from orchestrator.loop import TaskReport


def _report():
    return TaskReport(1, "feat/1-x", "approved", "approved", None, 1, "s", [])


def test_dump_before_down_on_success():
    order = []
    orchestrate(run_task_fn=lambda: _report(),
                up=lambda: order.append("up"),
                down=lambda: order.append("down"),
                dump=lambda: order.append("dump"))
    assert order == ["up", "dump", "down"]


def test_dump_failure_skips_down(capsys):
    order = []
    def dump(): order.append("dump"); raise subprocess.CalledProcessError(1, "dump")
    with pytest.raises(subprocess.CalledProcessError):
        orchestrate(run_task_fn=lambda: _report(),
                    up=lambda: order.append("up"),
                    down=lambda: order.append("down"), dump=dump)
    assert "down" not in order
    assert "stack" in capsys.readouterr().err.lower()


def test_run_task_raises_still_dumps():
    order = []
    def boom(): order.append("run"); raise RuntimeError("kaboom")
    with pytest.raises(RuntimeError):
        orchestrate(run_task_fn=boom,
                    up=lambda: order.append("up"),
                    down=lambda: order.append("down"),
                    dump=lambda: order.append("dump"))
    assert order == ["up", "run", "dump", "down"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd orchestrator && uv run pytest tests/test_orchestrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.orchestrate'`

- [ ] **Step 3: Write the implementation**

```python
# orchestrator/src/orchestrator/orchestrate.py
import subprocess
import sys
from typing import Callable

from orchestrator.loop import TaskReport


def orchestrate(*, run_task_fn: Callable[[], TaskReport],
                up: Callable[[], None], down: Callable[[], None],
                dump: Callable[[], None]) -> TaskReport:
    up()
    try:
        return run_task_fn()
    finally:
        try:
            dump()
        except subprocess.CalledProcessError:
            print(
                "WARNING: snapshot dump failed; leaving the stack UP so memory is "
                "not lost. Inspect the db, then tear down manually once dumped.",
                file=sys.stderr,
            )
            raise
        else:
            down()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd orchestrator && uv run pytest tests/test_orchestrate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/orchestrator/orchestrate.py orchestrator/tests/test_orchestrate.py
git commit -m "feat(orchestrator): lifecycle bracket with dump-before-down error handling"
```

---

### Task 7: `agentctl` CLI

The entry point: parse `--role` + task file, resolve the project, wire the real collaborators, run, and print the report.

**Files:**
- Create: `orchestrator/src/orchestrator/cli.py`
- Test: `orchestrator/tests/test_cli.py`

**Interfaces:**
- Consumes: `run_task` (Task 5), `orchestrate` (Task 6), seams (Task 4), branch ops (Task 3), `TaskRepository` (Task 1), `Settings`/`connect`.
- Produces: `main(argv: list[str] | None = None) -> int` and the `agentctl` console script (defined in Task 2's pyproject).

- [ ] **Step 1: Write the failing test**

```python
# orchestrator/tests/test_cli.py
import pytest

from orchestrator import cli
from orchestrator.loop import TaskReport


def test_main_parses_and_invokes(monkeypatch, tmp_path, capsys):
    task = tmp_path / "t.md"; task.write_text("# Do it\n")
    captured = {}

    def fake_run(*, project_dir, role, task_file, compose_dir):
        captured.update(project_dir=project_dir, role=role,
                        task_file=task_file, compose_dir=compose_dir)
        return TaskReport(3, "feat/3-do-it", "approved", "approved", None, 1, "done", [])

    monkeypatch.setattr(cli, "run_one_task", fake_run)
    rc = cli.main(["run", "--role", "developer", str(task)])
    assert rc == 0
    assert captured["role"] == "developer"
    assert captured["task_file"] == str(task)
    out = capsys.readouterr().out
    assert "feat/3-do-it" in out and "approved" in out


def test_main_rejects_bad_role(tmp_path):
    task = tmp_path / "t.md"; task.write_text("# x\n")
    with pytest.raises(SystemExit):  # argparse rejects an invalid --role choice
        cli.main(["run", "--role", "wizard", str(task)])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd orchestrator && uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator.cli'`

- [ ] **Step 3: Write the implementation**

```python
# orchestrator/src/orchestrator/cli.py
import argparse
import os
import sys
from pathlib import Path

from memory.config import Settings
from memory.db import connect
from memory.tasks import TaskRepository

from orchestrator import seams
from orchestrator.branch import checkout, create_task_branch, current_branch
from orchestrator.loop import TaskReport, run_task
from orchestrator.orchestrate import orchestrate

ROLES = ("developer",)  # the worker role; the reviewer pass is automatic
COMPOSE_DIR_DEFAULT = str(Path(__file__).resolve().parents[3])  # the agent_image repo root


def run_one_task(*, project_dir: str, role: str, task_file: str, compose_dir: str) -> TaskReport:
    base = current_branch(project_dir)

    def make_branch(branch: str) -> None:
        create_task_branch(project_dir, branch, base=base)

    def run_developer(task_id: int, round: int):
        return seams.exec_runner("developer", task_id, round,
                                 compose_dir=compose_dir, project_dir=project_dir)

    def run_reviewer(task_id: int, round: int):
        return seams.exec_runner("reviewer", task_id, round,
                                 compose_dir=compose_dir, project_dir=project_dir)

    def do_task() -> TaskReport:
        # Connect only after the stack is up (orchestrate calls this after `up`),
        # so the db on localhost:5432 is reachable.
        conn = connect(Settings.from_env())
        try:
            return run_task(task_file, role, repo=TaskRepository(conn),
                            make_branch=make_branch, run_developer=run_developer,
                            run_reviewer=run_reviewer)
        finally:
            conn.close()

    try:
        return orchestrate(
            run_task_fn=do_task,
            up=lambda: seams.compose_up(compose_dir=compose_dir, project_dir=project_dir),
            down=lambda: seams.compose_down(compose_dir=compose_dir, project_dir=project_dir),
            dump=lambda: seams.snapshot_dump(compose_dir=compose_dir, project_dir=project_dir),
        )
    finally:
        # Leave the user's working branch untouched: the task work stays on its
        # own branch; restore the base branch they started on. Best-effort.
        try:
            checkout(project_dir, base)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentctl")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one task through the dev->review loop")
    run.add_argument("--role", required=True, choices=ROLES)
    run.add_argument("task_file")

    ns = parser.parse_args(argv)  # argv=None reads sys.argv (incl. the `run` subcommand)

    project_dir = os.getcwd()
    compose_dir = os.environ.get("AGENT_IMAGE_DIR", COMPOSE_DIR_DEFAULT)
    report = run_one_task(project_dir=project_dir, role=ns.role,
                          task_file=ns.task_file, compose_dir=compose_dir)
    print(f"task #{report.task_id}: {report.status}")
    print(f"  branch:        {report.branch}")
    print(f"  review:        {report.review_status} (rounds: {report.rounds})")
    if report.review_notes:
        print(f"  review notes:  {report.review_notes}")
    if report.summary:
        print(f"  summary:       {report.summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd orchestrator && uv run pytest tests/test_cli.py -v`
Expected: PASS (2 passed). `test_main_parses_and_invokes` confirms arg parsing + the report print; `test_main_rejects_bad_role` confirms argparse raises `SystemExit` on an invalid `--role`.

- [ ] **Step 5: Commit**

```bash
git add orchestrator/src/orchestrator/cli.py orchestrator/tests/test_cli.py
git commit -m "feat(orchestrator): agentctl CLI entry point"
```

---

### Task 8: The role SDK runner (`memory/agent_runner.py`)

The generic, role-branched runner exec'd into a warm container. Drives a Claude Agent SDK session (lazy import), then writes its result to the `tasks` row.

**Files:**
- Create: `memory-service/src/memory/agent_runner.py`
- Test: `memory-service/tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `TaskRepository` (Task 1), `Settings`/`connect`.
- Produces:
  - `parse_verdict(text: str) -> tuple[str, str]` — returns `(review_status, notes)`; `review_status ∈ {"approved","needs_changes"}`; defaults to `needs_changes` when no `VERDICT:` line is found.
  - `build_prompt(role: str, task_text: str, review_notes: str | None, branch: str) -> str`
  - `run(role: str, task_id: int, round: int, *, repo, workspace: str, run_session, head_sha) -> None` — the testable core (collaborators injected).
  - `main() -> int` and `__main__` — wires real collaborators (`run_session` = the lazy SDK call; `head_sha` = git).

- [ ] **Step 1: Write the failing tests**

```python
# memory-service/tests/test_agent_runner.py
from memory import agent_runner
from memory.tasks import TaskRepository


def test_parse_verdict_approved():
    assert agent_runner.parse_verdict("looks good\nVERDICT: approved")[0] == "approved"


def test_parse_verdict_needs_changes_with_notes():
    status, notes = agent_runner.parse_verdict("issues:\n- x\nVERDICT: needs_changes")
    assert status == "needs_changes"
    assert "issues" in notes


def test_parse_verdict_defaults_to_needs_changes():
    assert agent_runner.parse_verdict("no verdict line here")[0] == "needs_changes"


def test_build_prompt_developer_includes_notes():
    p = agent_runner.build_prompt("developer", "do X", "fix the bug", "feat/1-x")
    assert "do X" in p and "fix the bug" in p


def test_run_developer_records_result(conn, tmp_path):
    repo = TaskRepository(conn)
    (tmp_path / "t.md").write_text("# T\n\ndo it")
    tid = repo.create("t.md", "T", "developer")  # spec_ref relative to workspace
    repo.set_branch(tid, "feat/1-t")
    agent_runner.run("developer", tid, 1, repo=repo, workspace=str(tmp_path),
                     run_session=lambda prompt: "I implemented it.",
                     head_sha=lambda ws: "abc123")
    row = repo.get(tid)
    assert row["summary"] == "I implemented it."
    assert row["artifacts"] == ["abc123"]


def test_run_reviewer_records_verdict(conn, tmp_path):
    repo = TaskRepository(conn)
    (tmp_path / "t.md").write_text("# T\n\ndo it")
    tid = repo.create("t.md", "T", "developer")
    repo.set_branch(tid, "feat/1-t")
    agent_runner.run("reviewer", tid, 1, repo=repo, workspace=str(tmp_path),
                     run_session=lambda prompt: "all good\nVERDICT: approved",
                     head_sha=lambda ws: "abc123")
    row = repo.get(tid)
    assert row["review_status"] == "approved"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd memory-service && uv run pytest tests/test_agent_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.agent_runner'`

- [ ] **Step 3: Write the implementation**

```python
# memory-service/src/memory/agent_runner.py
"""Generic role runner exec'd into a warm agent container. Drives a Claude Agent
SDK session (lazy import) and writes the result to the task's row. Role-branched:
developer commits + records summary/artifacts; reviewer records a verdict."""
import argparse
import os
import re
import subprocess
import sys

from memory.config import Settings
from memory.db import connect
from memory.tasks import TaskRepository

_VERDICT = re.compile(r"^VERDICT:\s*(approved|needs_changes)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_verdict(text: str) -> tuple[str, str]:
    matches = _VERDICT.findall(text)
    status = matches[-1].lower() if matches else "needs_changes"
    return status, text


def build_prompt(role: str, task_text: str, review_notes: str | None, branch: str) -> str:
    if role == "developer":
        prompt = (
            f"Implement this task. Work on the current git branch ({branch}) and "
            f"commit your changes when done.\n\n--- TASK ---\n{task_text}\n"
        )
        if review_notes:
            prompt += f"\n--- REVIEWER NOTES FROM THE PREVIOUS ROUND ---\n{review_notes}\n"
        return prompt
    return (
        f"Review the changes on branch {branch} against the task below. Inspect the "
        f"diff and the code. End your response with a single line exactly "
        f"'VERDICT: approved' or 'VERDICT: needs_changes', followed by your notes.\n\n"
        f"--- TASK ---\n{task_text}\n"
    )


def run(role: str, task_id: int, round: int, *, repo, workspace: str,
        run_session, head_sha) -> None:
    row = repo.get(task_id)
    task_text = _read_task_text(workspace, row["spec_ref"])
    prompt = build_prompt(role, task_text, row["review_notes"], row["branch"])
    output = run_session(prompt)
    if role == "developer":
        repo.record_developer_result(task_id, summary=output, artifacts=[head_sha(workspace)])
    else:
        status, notes = parse_verdict(output)
        repo.record_review(task_id, review_status=status, review_notes=notes)


def _read_task_text(workspace: str, spec_ref: str) -> str:
    path = spec_ref if os.path.isabs(spec_ref) else os.path.join(workspace, spec_ref)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _head_sha(workspace: str) -> str:
    return subprocess.run(
        ["git", "-C", workspace, "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _run_session(prompt: str) -> str:
    """Run one Claude Agent SDK session under Python control; return the final
    assistant text. SDK imported lazily so this module loads without the SDK."""
    import asyncio

    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query,
    )

    options = ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code"},
        cwd=os.environ.get("WORKSPACE", "/workspace"),
        permission_mode="bypassPermissions",
        model=os.environ.get("AGENT_MODEL", "claude-opus-4-8"),
    )

    async def _go() -> str:
        final = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final = block.text
            elif isinstance(message, ResultMessage):
                break
        return final

    return asyncio.run(_go())


def main() -> int:
    parser = argparse.ArgumentParser(prog="memory.agent_runner")
    parser.add_argument("--role", required=True, choices=("developer", "reviewer"))
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--round", type=int, required=True)
    ns = parser.parse_args()

    conn = connect(Settings.from_env())
    try:
        run(ns.role, ns.task_id, ns.round, repo=TaskRepository(conn),
            workspace=os.environ.get("WORKSPACE", "/workspace"),
            run_session=_run_session, head_sha=_head_sha)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd memory-service && uv run pytest tests/test_agent_runner.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/agent_runner.py memory-service/tests/test_agent_runner.py
git commit -m "feat(runner): generic role SDK runner writing results to the tasks row"
```

---

### Task 9: agent-worker image (Agent SDK) + warm `developer`/`reviewer` services

Add the Claude Agent SDK to the agent-worker image and declare the two long-lived role containers the orchestrator `exec`s into.

**Files:**
- Modify: `agent-worker/Dockerfile` (install `claude-agent-sdk` into the `/opt/memory` venv)
- Modify: `docker-compose.yml` (root) — add `developer` and `reviewer` services

> **Existing-code change.** Image build + runtime topology change; documented in Task 10.

**Interfaces:**
- Consumes: `memory.agent_runner` (Task 8) as the exec target; the Q1 `init` gate.
- Produces: warm `developer` and `reviewer` services (one per role), each long-lived, with the project mounted read-write at `/workspace`, `AGENT_ROLE` set, and `/opt/memory/.venv` carrying `claude-agent-sdk`.

- [ ] **Step 1: Add the Agent SDK to the agent-worker image**

In `agent-worker/Dockerfile`, immediately after the `RUN cd /opt/memory && uv sync` line, add:

```dockerfile
# Claude Agent SDK (Python) for the role runner — added ONLY to the agent-worker
# image's memory venv, so the lean memory-service image stays SDK-free.
RUN cd /opt/memory && uv pip install claude-agent-sdk
```

- [ ] **Step 2: Add the warm role services to the root compose**

In `docker-compose.yml`, add these two services (after `agent-worker`), mirroring the `agent-worker` env but long-lived and role-pinned:

```yaml
  developer:
    build:
      context: .
      dockerfile: agent-worker/Dockerfile
    depends_on:
      db:
        condition: service_healthy
      embeddings:
        condition: service_healthy
      init:
        condition: service_completed_successfully
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
      AGENT_ROLE: developer
      CLAUDE_CODE_OAUTH_TOKEN: ${CLAUDE_CODE_OAUTH_TOKEN:-}
      BOOTSTRAP: ${BOOTSTRAP:-auto}
    volumes:
      - ${PROJECT_DIR:-./}:/workspace
      - mise-data:/opt/mise/data
    command: ["sleep", "infinity"]   # warm; orchestrator exec's the runner per task

  reviewer:
    build:
      context: .
      dockerfile: agent-worker/Dockerfile
    depends_on:
      db:
        condition: service_healthy
      embeddings:
        condition: service_healthy
      init:
        condition: service_completed_successfully
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
      AGENT_ROLE: reviewer
      CLAUDE_CODE_OAUTH_TOKEN: ${CLAUDE_CODE_OAUTH_TOKEN:-}
      BOOTSTRAP: ${BOOTSTRAP:-auto}
    volumes:
      - ${PROJECT_DIR:-./}:/workspace
      - mise-data:/opt/mise/data
    command: ["sleep", "infinity"]
```

Note: the entrypoint runs first (composes `CLAUDE.md` + skills + registers the memory MCP), then `exec`s `sleep infinity` — so each warm container is fully role-configured and idle, ready for `docker compose exec`.

- [ ] **Step 3: Validate compose config**

Run: `cd "/Users/hirenppp/Documents/Claude Escapades/agent_image" && PROJECT_DIR=$(pwd) docker compose config >/dev/null && echo "compose OK"`
Expected: `compose OK` (no YAML/interpolation errors; `developer` and `reviewer` present).

- [ ] **Step 4: Build the agent-worker image and verify the SDK imports**

Run: `cd "/Users/hirenppp/Documents/Claude Escapades/agent_image" && PROJECT_DIR=$(pwd) docker compose build developer && PROJECT_DIR=$(pwd) docker compose run --rm --no-deps --entrypoint "" developer /opt/memory/.venv/bin/python -c "import claude_agent_sdk, memory.agent_runner; print('runner+sdk import OK')"`
Expected: build succeeds; prints `runner+sdk import OK` (the SDK and the runner module both import inside the image).

- [ ] **Step 5: Commit**

```bash
git add agent-worker/Dockerfile docker-compose.yml
git commit -m "feat(compose): add Agent SDK to image + warm developer/reviewer services"
```

---

### Task 10: Docs update + end-to-end smoke script

Document the orchestration slice and provide a scripted end-to-end smoke check (à la phase-2's `smoke.sh`), kept out of the pytest unit suites.

**Files:**
- Modify: `docs/architecture.md` (new orchestration section + topology/service notes)
- Create: `orchestrator/smoke.sh`

**Interfaces:**
- Consumes: everything built in Tasks 1–9.

- [ ] **Step 1: Add an orchestration section to architecture.md**

In `docs/architecture.md`, after the "Diagram 4 — Data flow B" section, add:

```markdown
## Diagram 5 — Single-task orchestration (Q2 increment 1)

`agentctl run --role developer path/to/task.md` drives one task end-to-end. A
host-side Python orchestrator brings the stack up (Q1 `init` restores/seeds
memory), runs the task on a dedicated `feat/<id>-<tagline>` branch through a
developer agent, has a reviewer agent check it, auto-iterates up to a cap
(default 2), then dumps the snapshot and tears down.

\```mermaid
flowchart TB
    CLI["agentctl run --role developer task.md"] --> UP["docker compose up --wait<br/>(db, embeddings, init, developer, reviewer)"]
    UP --> ROW["INSERT tasks row → id; create branch feat/&lt;id&gt;-&lt;tagline&gt;"]
    ROW --> DEV["docker compose exec developer<br/>python -m memory.agent_runner (SDK session)"]
    DEV --> REV["docker compose exec reviewer<br/>→ VERDICT: approved | needs_changes"]
    REV -->|approved or cap reached| DUMP["memory.snapshot dump (Q1)"]
    REV -->|needs_changes & round < cap| DEV
    DUMP --> DOWN["docker compose down"]
    DOWN --> REPORT["report branch, review verdict, summary"]
\```

- **Coordination is through the `tasks` table** (in the memory Postgres, so it is
  snapshotted by Q1) plus the task branch — agents update their row; the
  orchestrator reads it back between steps.
- **The orchestrator is plain host-side Python**, not an LLM. Agents run as
  Claude Agent SDK sessions inside warm per-role containers.
- **Never lossy:** the snapshot `dump` always runs before `down`; if it fails,
  the stack stays up and the failure is surfaced.
\```
```

(Write a real single mermaid block — strip the backslash-escapes from the example above and ensure the file's ``` fences stay balanced.)

- [ ] **Step 2: Add the warm services + `agentctl` to the topology notes**

In `docs/architecture.md`, under Diagram 1's "How to read it" list, append:

```markdown
- **Orchestration (Q2):** `agentctl` (host) drives the stack; warm `developer`
  and `reviewer` containers (same agent-worker image, role-pinned, `sleep
  infinity`) receive a fresh Claude Agent SDK session per task via `docker
  compose exec`. Coordination state lives in the `tasks` table.
```

- [ ] **Step 3: Create the end-to-end smoke script**

`orchestrator/smoke.sh`:

```bash
#!/usr/bin/env bash
# End-to-end smoke check for the single-task orchestrator. NOT part of the pytest
# unit suite. Requires Docker, a built stack, and CLAUDE_CODE_OAUTH_TOKEN in .env.
#
# Usage: PROJECT=/path/to/throwaway/git/repo bash orchestrator/smoke.sh
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT to a throwaway git repo path}"
AGENT_IMAGE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# A trivial task the developer can complete in one shot.
mkdir -p "$PROJECT/tasks"
cat > "$PROJECT/tasks/smoke.md" <<'EOF'
# Add a greeting file

Create a file `HELLO.txt` at the repo root containing the single line `hello`.
EOF

cd "$PROJECT"
git add tasks/smoke.md && git commit -m "add smoke task" >/dev/null 2>&1 || true

echo "== running agentctl =="
PROJECT_DIR="$PROJECT" AGENT_IMAGE_DIR="$AGENT_IMAGE_DIR" \
  uv --directory "$AGENT_IMAGE_DIR/orchestrator" run agentctl run \
  --role developer tasks/smoke.md

echo "== assertions =="
git -C "$PROJECT" rev-parse --verify --quiet "refs/heads/$(git -C "$PROJECT" branch --list 'feat/*' | tr -d ' *' | head -1)" \
  && echo "PASS: task branch exists"
docker compose -f "$AGENT_IMAGE_DIR/docker-compose.yml" ps --status running --quiet \
  | grep -q . && echo "WARN: stack still up (check teardown)" || echo "PASS: stack torn down"
echo "smoke complete"
```

- [ ] **Step 4: Make the smoke script executable and shellcheck-clean**

Run: `cd "/Users/hirenppp/Documents/Claude Escapades/agent_image" && chmod +x orchestrator/smoke.sh && bash -n orchestrator/smoke.sh && echo "syntax OK"`
Expected: `syntax OK` (the script is not executed end-to-end here — it needs a live stack + token; this only checks shell syntax).

- [ ] **Step 5: Verify the architecture mermaid fences are balanced**

Run: `cd "/Users/hirenppp/Documents/Claude Escapades/agent_image" && python3 -c "import sys; n=sum(1 for l in open('docs/architecture.md') if l.strip().startswith('\`\`\`')); print('fences:', n, 'balanced' if n%2==0 else 'UNBALANCED'); sys.exit(0 if n%2==0 else 1)"`
Expected: `balanced`

- [ ] **Step 6: Commit**

```bash
git add docs/architecture.md orchestrator/smoke.sh
git commit -m "docs: document single-task orchestration + add e2e smoke script"
```

---

## Final verification

- [ ] **Run both unit suites**

Run:
```
cd memory-service && docker compose up -d db && export PATH="/opt/homebrew/opt/postgresql@18/bin:$PATH" && uv run pytest -q
cd ../orchestrator && uv run pytest -q
```
Expected: both suites pass — memory-service (existing + `test_tasks_repository`, `test_agent_runner`, the new snapshot round-trip test) and orchestrator (`test_branch_name`, `test_branch_git`, `test_seams`, `test_loop`, `test_orchestrate`, `test_cli`).
