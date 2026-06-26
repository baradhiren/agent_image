# Per-Project Snapshot-able Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the memory database per-project, co-located with the source under a self-ignoring `.agent-memory/`, snapshot-able on demand, and auto-restored (or re-seeded) on startup — never silently losing agent-authored knowledge.

**Architecture:** A new `memory/snapshot.py` wraps `pg_dump -Fc` / `pg_restore` against `DATABASE_URL` and guards restores with a `meta.json` compatibility check. A new `memory/startup.py` one-shot resets the live DB (for isolation), probes `.agent-memory/` writability (fall back to a named volume), restores a compatible snapshot or seeds fresh, then always runs an incremental `reconcile` to catch up. Compose gains a one-shot `init` service that runs startup before the steady worker drains.

**Tech Stack:** Python 3.12, psycopg 3, PostgreSQL 16 + pgvector, `pg_dump`/`pg_restore` (PostgreSQL client 16), Docker Compose, pytest.

## Global Constraints

- **Source spec:** [docs/superpowers/specs/2026-06-25-per-project-snapshot-memory-design.md](../specs/2026-06-25-per-project-snapshot-memory-design.md). This plan implements the **Q1** spec only; the orchestration appendix (Q2–Q4) is explicitly out of scope.
- **All code lives under** `memory-service/` (package root `memory-service/src/memory/`, tests `memory-service/tests/`, SQL `memory-service/sql/`).
- **"Agent-authored knowledge" in this codebase = the `spec_links` table** (written by the MCP `add_knowledge` tool). Round-trip tests MUST assert `spec_links` rows survive. There is no separate `knowledge` table.
- **Snapshot format:** `pg_dump --format=custom` (compressed custom). Restores require a `pg_restore` whose major version matches the server (**16**).
- **Compatibility guard fields (verbatim):** `schema_version` (int, current = `1`), `pg_major` (int), `code_embed.{model,dim}`, `doc_embed.{model,dim}`. A restore is refused if any differ from the running `Settings` / server.
- **Provider is NOT a compatibility field.** A provider-only change (e.g. `local` ↔ `remote`, same model + dim) yields the same vector space, so it must not block a restore. Because the restored `embedding_config` row carries the dump-time provider, the catch-up reconcile would otherwise crash on `ensure_embedding_config`. `ensure_embedding_config` must therefore **update** the stored provider on a provider-only change and keep only model/dim changes fatal (**Task 4b**).
- **Self-ignoring dir:** `.agent-memory/` MUST contain a `.gitignore` whose only content is `*` so the whole directory ignores itself. Never touch the repo's root `.gitignore`.
- **Atomicity:** every file the snapshot writes (`snapshot.dump`, `meta.json`) is written to a temp file in the same dir and `os.replace`d into place. `dump` raises (non-zero CLI exit) on any failure; the caller must NOT tear down on a failed dump.
- **Degrade loud, never lossy:** unwritable `.agent-memory/` → fall back to named volume `agent-memory` (mounted at `/agent-memory`) and warn on stderr; corrupt/incompatible snapshot → seed fresh (derivable data only). Agent knowledge is never silently dropped.
- **Settings source of truth:** [memory-service/src/memory/config.py](../../../memory-service/src/memory/config.py) — `Settings.code_embed.{model,dim}`, `Settings.doc_embed.{model,dim}`, `Settings.database_url`.

### Test prerequisites (read before running any DB test)

- A pg16 server must be running: from `memory-service/`, `docker compose up -d db` (exposes `localhost:5432`; matches the default `DATABASE_URL`).
- `pg_dump` / `pg_restore` **major 16** MUST be on `PATH` for the dump/restore tasks. The host default may be older (e.g. Homebrew 14), which cannot dump a pg16 server. Fix locally with `brew install postgresql@16` and prepend its `bin` to `PATH`, **or** run the suite inside the memory container (which installs `postgresql-client-16` after Task 8). The Task 1 and Task 2 tests do not need `pg_dump`.
- Run tests from `memory-service/`: `uv run pytest ...`.

---

### Task 1: Compatibility metadata (pure, no Postgres)

Build the `meta.json` payload and the pure compatibility predicate. Pure functions, unit-testable without a database.

**Files:**
- Create: `memory-service/src/memory/snapshot.py`
- Test: `memory-service/tests/test_snapshot_meta.py`

**Interfaces:**
- Consumes: `memory.config.Settings`, `Settings.code_embed.{model,dim}`, `Settings.doc_embed.{model,dim}`.
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `build_meta(settings: Settings, pg_major: int, source_head: str | None, location: str) -> dict`
  - `meta_is_compatible(meta: dict, settings: Settings, pg_major: int) -> bool` (spec lists this as `meta_is_compatible(meta, settings)`; `pg_major` is threaded in as a parameter to keep the function Postgres-free and pure).

- [ ] **Step 1: Write the failing test**

```python
# memory-service/tests/test_snapshot_meta.py
from datetime import datetime

from memory.config import EmbedConfig, Settings
from memory import snapshot


def _settings(code_model="BAAI/bge-small-en-v1.5", code_dim=384,
              doc_model="BAAI/bge-small-en-v1.5", doc_dim=384) -> Settings:
    return Settings(
        database_url="postgresql://x/y",
        code_embed=EmbedConfig("remote", code_model, code_dim, "http://e:80"),
        doc_embed=EmbedConfig("remote", doc_model, doc_dim, "http://e:80"),
    )


def test_build_meta_has_required_fields():
    meta = snapshot.build_meta(_settings(), pg_major=16,
                               source_head="abc123", location="co-located")
    assert meta["schema_version"] == snapshot.SCHEMA_VERSION
    assert meta["pg_major"] == 16
    assert meta["code_embed"] == {"model": "BAAI/bge-small-en-v1.5", "dim": 384}
    assert meta["doc_embed"] == {"model": "BAAI/bge-small-en-v1.5", "dim": 384}
    assert meta["source_head"] == "abc123"
    assert meta["location"] == "co-located"
    # created_at is a parseable ISO-8601 timestamp
    datetime.fromisoformat(meta["created_at"])


def test_compatible_when_everything_matches():
    s = _settings()
    meta = snapshot.build_meta(s, 16, None, "co-located")
    assert snapshot.meta_is_compatible(meta, s, pg_major=16) is True


def test_incompatible_on_dim_mismatch():
    meta = snapshot.build_meta(_settings(code_dim=384), 16, None, "co-located")
    assert snapshot.meta_is_compatible(meta, _settings(code_dim=768), 16) is False


def test_incompatible_on_model_mismatch():
    meta = snapshot.build_meta(_settings(doc_model="m-a"), 16, None, "co-located")
    assert snapshot.meta_is_compatible(meta, _settings(doc_model="m-b"), 16) is False


def test_incompatible_on_pg_major_mismatch():
    s = _settings()
    meta = snapshot.build_meta(s, 16, None, "co-located")
    assert snapshot.meta_is_compatible(meta, s, pg_major=15) is False


def test_incompatible_on_schema_version_mismatch():
    s = _settings()
    meta = snapshot.build_meta(s, 16, None, "co-located")
    meta["schema_version"] = 999
    assert snapshot.meta_is_compatible(meta, s, 16) is False


def test_incompatible_on_garbage_meta():
    assert snapshot.meta_is_compatible({}, _settings(), 16) is False
    assert snapshot.meta_is_compatible({"code_embed": None}, _settings(), 16) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd memory-service && uv run pytest tests/test_snapshot_meta.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.snapshot'`

- [ ] **Step 3: Write minimal implementation**

```python
# memory-service/src/memory/snapshot.py
"""Snapshot the memory DB to disk and restore it, guarded by a compatibility
check. The on-disk snapshot is a cache + a knowledge store: derivable data can
be rebuilt by reconcile, but agent-authored spec_links must never be lost."""
from __future__ import annotations

from datetime import datetime, timezone

from memory.config import Settings

SCHEMA_VERSION = 1


def build_meta(settings: Settings, pg_major: int, source_head: str | None,
               location: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "pg_major": pg_major,
        "code_embed": {"model": settings.code_embed.model, "dim": settings.code_embed.dim},
        "doc_embed": {"model": settings.doc_embed.model, "dim": settings.doc_embed.dim},
        "source_head": source_head,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "location": location,
    }


def meta_is_compatible(meta: dict, settings: Settings, pg_major: int) -> bool:
    try:
        if meta.get("schema_version") != SCHEMA_VERSION:
            return False
        if meta.get("pg_major") != pg_major:
            return False
        for key, cfg in (("code_embed", settings.code_embed),
                         ("doc_embed", settings.doc_embed)):
            block = meta.get(key) or {}
            if block.get("model") != cfg.model or block.get("dim") != cfg.dim:
                return False
        return True
    except (AttributeError, TypeError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd memory-service && uv run pytest tests/test_snapshot_meta.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/snapshot.py memory-service/tests/test_snapshot_meta.py
git commit -m "feat(snapshot): add meta.json builder + pure compatibility guard"
```

---

### Task 2: `reset_db` — clean-slate helper for isolation

Add the DROP-SCHEMA reset that guarantees per-project isolation (the snapshot, not whatever the global volume held, is the source of truth on every start).

**Files:**
- Modify: `memory-service/src/memory/db.py` (add `reset_db`)
- Test: `memory-service/tests/test_reset_db.py`

> **Existing-code change.** `db.py` is documented indirectly in [docs/architecture.md](../../architecture.md) (Diagram 1, "Where the database lives"). The architecture update for the new reset/restore behavior is handled in Task 9.

**Interfaces:**
- Consumes: `memory.db.connect`, `memory.config.Settings`.
- Produces: `reset_db(conn: psycopg.Connection) -> None` — drops and recreates `public`, removing all memory tables.

- [ ] **Step 1: Write the failing test**

```python
# memory-service/tests/test_reset_db.py
from memory.config import Settings
from memory.db import apply_schema, connect, reset_db


def test_reset_db_drops_all_tables():
    conn = connect(Settings.from_env())
    apply_schema(conn)  # creates files, symbols, ... tables
    conn.execute("INSERT INTO files (path, language, content_hash) VALUES ('x.py','python','h')")
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1

    reset_db(conn)

    remaining = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public'"
    ).fetchone()[0]
    conn.close()
    assert remaining == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd memory-service && docker compose up -d db && uv run pytest tests/test_reset_db.py -v`
Expected: FAIL — `ImportError: cannot import name 'reset_db'`

- [ ] **Step 3: Write minimal implementation**

Add to `memory-service/src/memory/db.py` (after `apply_schema`):

```python
def reset_db(conn: psycopg.Connection) -> None:
    """Drop every memory table (and the vector extension's objects) so the next
    restore/seed starts from a clean slate. This is what enforces per-project
    isolation: the snapshot — not the previous volume contents — is the source
    of truth on every start."""
    conn.execute("DROP SCHEMA public CASCADE")
    conn.execute("CREATE SCHEMA public")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd memory-service && uv run pytest tests/test_reset_db.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/db.py memory-service/tests/test_reset_db.py
git commit -m "feat(db): add reset_db clean-slate helper for per-project isolation"
```

---

### Task 3: `dump()` — write snapshot.dump + meta.json + self-ignoring .gitignore

Atomic `pg_dump -Fc` plus the compatibility metadata and the self-ignoring `.gitignore`.

**Files:**
- Modify: `memory-service/src/memory/snapshot.py` (add `server_pg_major`, `_ensure_gitignore`, `dump`)
- Test: `memory-service/tests/test_snapshot_dump.py`

> **Requires `pg_dump` major 16 on PATH** (see Test prerequisites).

**Interfaces:**
- Consumes: `memory.db.connect`, `build_meta` (Task 1).
- Produces:
  - `server_pg_major(conn) -> int`
  - `dump(target_dir: str, settings: Settings, source_head: str | None = None, location: str = "co-located") -> None` — writes `snapshot.dump` + `meta.json` into `target_dir`, creating `.gitignore` (`*`) if missing; raises on any failure.

- [ ] **Step 1: Write the failing test**

```python
# memory-service/tests/test_snapshot_dump.py
import json

import pytest

from memory.config import EmbedConfig, Settings
from memory.db import apply_schema, connect, reset_db
from memory import snapshot


def _seed_db():
    settings = Settings.from_env()
    conn = connect(settings)
    reset_db(conn)
    apply_schema(conn)
    conn.execute("INSERT INTO files (path, language, content_hash) VALUES ('a.py','python','h1')")
    conn.execute("INSERT INTO spec_links (spec_path, symbol_qualname) VALUES ('spec.md','a.fn')")
    conn.close()
    return settings


def test_dump_writes_all_three_files(tmp_path):
    settings = _seed_db()
    snapshot.dump(str(tmp_path), settings, source_head="deadbeef")

    assert (tmp_path / "snapshot.dump").exists()
    assert (tmp_path / "snapshot.dump").stat().st_size > 0
    assert (tmp_path / ".gitignore").read_text().strip() == "*"

    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["pg_major"] == 16
    assert meta["code_embed"]["dim"] == 384
    assert meta["source_head"] == "deadbeef"


def test_dump_does_not_clobber_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("custom-content\n")
    settings = _seed_db()
    snapshot.dump(str(tmp_path), settings)
    assert (tmp_path / ".gitignore").read_text() == "custom-content\n"


def test_dump_raises_on_bad_database(tmp_path):
    settings = Settings(
        database_url="postgresql://postgres:postgres@localhost:5432/nope_no_db",
        code_embed=EmbedConfig("remote", "m", 384, None),
        doc_embed=EmbedConfig("remote", "m", 384, None),
    )
    with pytest.raises(Exception):
        snapshot.dump(str(tmp_path), settings)
    # atomic: no half-written snapshot left behind
    assert not (tmp_path / "snapshot.dump").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd memory-service && uv run pytest tests/test_snapshot_dump.py -v`
Expected: FAIL — `AttributeError: module 'memory.snapshot' has no attribute 'dump'`

- [ ] **Step 3: Write minimal implementation**

Add imports at the top of `memory-service/src/memory/snapshot.py`:

```python
import json
import os
import subprocess
import tempfile
from pathlib import Path

from memory.db import connect
```

Add to `memory-service/src/memory/snapshot.py`:

```python
def server_pg_major(conn) -> int:
    row = conn.execute("SHOW server_version_num").fetchone()
    return int(row[0]) // 10000


def _ensure_gitignore(target: Path) -> None:
    gi = target / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")


def dump(target_dir: str, settings: Settings, source_head: str | None = None,
         location: str = "co-located") -> None:
    """Atomically write snapshot.dump + meta.json into target_dir, creating the
    self-ignoring .gitignore if missing. Raises on any failure so the caller can
    refuse to tear down on a failed dump."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(target)

    conn = connect(settings)
    try:
        pg_major = server_pg_major(conn)
    finally:
        conn.close()

    fd, tmp = tempfile.mkstemp(dir=str(target), suffix=".dump.tmp")
    os.close(fd)
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--dbname", settings.database_url,
             "--file", tmp],
            check=True,
        )
        os.replace(tmp, str(target / "snapshot.dump"))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    meta = build_meta(settings, pg_major, source_head, location)
    meta_tmp = str(target / "meta.json.tmp")
    with open(meta_tmp, "w") as f:
        json.dump(meta, f, indent=2)
    os.replace(meta_tmp, str(target / "meta.json"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd memory-service && uv run pytest tests/test_snapshot_dump.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/snapshot.py memory-service/tests/test_snapshot_dump.py
git commit -m "feat(snapshot): atomic dump with meta.json and self-ignoring .gitignore"
```

---

### Task 4: `restore()` — guarded pg_restore round-trip

Validate `meta.json`, reset the DB, and `pg_restore`; return `False` (caller seeds) when missing/incompatible/corrupt.

**Files:**
- Modify: `memory-service/src/memory/snapshot.py` (add `restore`)
- Test: `memory-service/tests/test_snapshot_restore.py`

> **Requires `pg_restore` major 16 on PATH.**

**Interfaces:**
- Consumes: `memory.db.connect`, `memory.db.reset_db` (Task 2), `meta_is_compatible`, `server_pg_major`, `dump` (Task 3).
- Produces: `restore(source_dir: str, settings: Settings) -> bool` — `True` on a successful compatible restore; `False` if snapshot absent, incompatible, or `pg_restore` fails.

- [ ] **Step 1: Write the failing test**

```python
# memory-service/tests/test_snapshot_restore.py
import json

from memory.config import Settings
from memory.db import apply_schema, connect, reset_db
from memory import snapshot


def _seed_and_dump(tmp_path):
    settings = Settings.from_env()
    conn = connect(settings)
    reset_db(conn)
    apply_schema(conn)
    conn.execute("INSERT INTO files (path, language, content_hash) VALUES ('a.py','python','h1')")
    conn.execute("INSERT INTO spec_links (spec_path, symbol_qualname) VALUES ('spec.md','a.fn')")
    conn.close()
    snapshot.dump(str(tmp_path), settings)
    return settings


def _counts(settings):
    conn = connect(settings)
    files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    links = conn.execute("SELECT count(*) FROM spec_links").fetchone()[0]
    conn.close()
    return files, links


def test_roundtrip_preserves_knowledge(tmp_path):
    settings = _seed_and_dump(tmp_path)
    # wipe, then restore
    conn = connect(settings); reset_db(conn); conn.close()

    assert snapshot.restore(str(tmp_path), settings) is True
    assert _counts(settings) == (1, 1)  # files AND spec_links survive


def test_restore_returns_false_when_no_snapshot(tmp_path):
    settings = Settings.from_env()
    assert snapshot.restore(str(tmp_path), settings) is False


def test_restore_refuses_incompatible_meta(tmp_path):
    settings = _seed_and_dump(tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text())
    meta["code_embed"]["dim"] = 768  # simulate model/dim swap
    (tmp_path / "meta.json").write_text(json.dumps(meta))

    conn = connect(settings); reset_db(conn); conn.close()
    assert snapshot.restore(str(tmp_path), settings) is False


def test_restore_returns_false_on_corrupt_dump(tmp_path):
    settings = _seed_and_dump(tmp_path)
    (tmp_path / "snapshot.dump").write_bytes(b"not a real pg_dump archive")

    conn = connect(settings); reset_db(conn); conn.close()
    assert snapshot.restore(str(tmp_path), settings) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd memory-service && uv run pytest tests/test_snapshot_restore.py -v`
Expected: FAIL — `AttributeError: module 'memory.snapshot' has no attribute 'restore'`

- [ ] **Step 3: Write minimal implementation**

Add `from memory.db import connect, reset_db` (update the existing `from memory.db import connect` import to include `reset_db`) and add to `memory-service/src/memory/snapshot.py`:

```python
def restore(source_dir: str, settings: Settings) -> bool:
    """Restore a compatible snapshot into a clean DB. Returns True on success;
    False (caller then seeds) if the snapshot is missing, incompatible, or
    pg_restore fails. Never raises on the seed-fallback paths — derivable data
    is safe to rebuild."""
    source = Path(source_dir)
    dump_path = source / "snapshot.dump"
    meta_path = source / "meta.json"
    if not dump_path.exists() or not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False

    conn = connect(settings)
    try:
        if not meta_is_compatible(meta, settings, server_pg_major(conn)):
            return False
        reset_db(conn)  # clean slate before restore
    finally:
        conn.close()

    try:
        subprocess.run(
            ["pg_restore", "--no-owner", "--dbname", settings.database_url,
             str(dump_path)],
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd memory-service && uv run pytest tests/test_snapshot_restore.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/snapshot.py memory-service/tests/test_snapshot_restore.py
git commit -m "feat(snapshot): guarded restore with meta check and seed fallback"
```

---

### Task 4b: Tolerate provider-only embedding-config changes (restore safety)

After a restore, the catch-up `reconcile` (Task 7) calls `ensure_embedding_config`. The restored `embedding_config` row carries the provider recorded at dump time; if the running stack uses a different provider with the same model+dim (e.g. snapshot `local`, stack `remote`), today's `ensure_embedding_config` raises `EmbeddingConfigMismatch` and crashes startup. The `meta.json` guard deliberately ignores provider (same model+dim = same vector space), so the repository must agree.

**Files:**
- Modify: `memory-service/src/memory/repository.py` (`ensure_embedding_config`)
- Test: `memory-service/tests/test_embedding_config_provider.py`

**Interfaces:**
- `ensure_embedding_config(collection, provider, model, dim) -> None` — unchanged signature. Now: no-op on exact match; **updates the stored `provider`** when only the provider differs; still raises `EmbeddingConfigMismatch` when `model` or `dim` differ.

- [ ] **Step 1: Write the failing test**

```python
# memory-service/tests/test_embedding_config_provider.py
import pytest

from memory.repository import EmbeddingConfigMismatch, Repository


def test_provider_only_change_updates_not_raises(conn):
    repo = Repository(conn)
    repo.ensure_embedding_config("code", "local", "bge", 384)
    repo.ensure_embedding_config("code", "remote", "bge", 384)  # provider-only: no raise
    assert repo.get_embedding_config("code") == {"provider": "remote", "model": "bge", "dim": 384}


def test_model_or_dim_change_still_raises(conn):
    repo = Repository(conn)
    repo.ensure_embedding_config("code", "local", "bge", 384)
    with pytest.raises(EmbeddingConfigMismatch):
        repo.ensure_embedding_config("code", "local", "bge", 768)    # dim differs
    with pytest.raises(EmbeddingConfigMismatch):
        repo.ensure_embedding_config("code", "local", "other", 384)  # model differs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd memory-service && uv run pytest tests/test_embedding_config_provider.py -v`
Expected: FAIL — `test_provider_only_change_updates_not_raises` raises `EmbeddingConfigMismatch` instead of updating.

- [ ] **Step 3: Write minimal implementation**

Replace the body of `ensure_embedding_config` in `memory-service/src/memory/repository.py`:

```python
    def ensure_embedding_config(self, collection: str, provider: str, model: str, dim: int) -> None:
        existing = self.get_embedding_config(collection)
        wanted = {"provider": provider, "model": model, "dim": dim}
        if existing is None:
            self._conn.execute(
                "INSERT INTO embedding_config (collection, provider, model, dim) VALUES (%s, %s, %s, %s)",
                (collection, provider, model, dim),
            )
        elif existing == wanted:
            return
        elif existing["model"] == model and existing["dim"] == dim:
            # provider-only change is safe (same model+dim = same vector space)
            self._conn.execute(
                "UPDATE embedding_config SET provider = %s WHERE collection = %s",
                (provider, collection),
            )
        else:
            raise EmbeddingConfigMismatch(
                f"{collection}: stored {existing} != configured {wanted}; reconcile/re-embed required"
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd memory-service && uv run pytest tests/test_embedding_config_provider.py tests/test_retrieval.py -v`
Expected: PASS (new tests pass; the existing `test_retrieval.py` mismatch test still raises because its model+dim differ).

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/repository.py memory-service/tests/test_embedding_config_provider.py
git commit -m "fix(repository): tolerate provider-only embedding-config change"
```

---

### Task 5: Snapshot CLI (`python -m memory.snapshot dump|restore <dir>`)

The explicit-save / explicit-restore entrypoint the lifecycle helper / orchestrator calls. `dump` must exit non-zero on failure so a caller never tears down after a failed dump.

**Files:**
- Modify: `memory-service/src/memory/snapshot.py` (add `_git_head`, `main`, `__main__` guard)
- Test: `memory-service/tests/test_snapshot_cli.py`

**Interfaces:**
- Consumes: `dump`, `restore`.
- Produces: CLI `python -m memory.snapshot dump <dir>` / `restore <dir>`. `dump` exits non-zero on failure; `restore` exits `0` whether it restored or fell back (prints the decision). `dump` records `source_head` from the git HEAD of `<dir>`'s parent when available.

- [ ] **Step 1: Write the failing test**

```python
# memory-service/tests/test_snapshot_cli.py
import subprocess
import sys


def _run(args, env_extra=None):
    import os
    # pytest runs from memory-service/; src/ holds the `memory` package.
    env = dict(os.environ, PYTHONPATH="src")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "memory.snapshot", *args],
        capture_output=True, text=True, env=env,
    )


def test_cli_dump_then_restore_roundtrip(tmp_path):
    dump = _run(["dump", str(tmp_path)])
    assert dump.returncode == 0, dump.stderr
    assert (tmp_path / "snapshot.dump").exists()

    restore = _run(["restore", str(tmp_path)])
    assert restore.returncode == 0, restore.stderr


def test_cli_dump_exits_nonzero_on_failure(tmp_path):
    bad = "postgresql://postgres:postgres@localhost:5432/nope_no_db"
    result = _run(["dump", str(tmp_path)], env_extra={"DATABASE_URL": bad})
    assert result.returncode != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd memory-service && uv run pytest tests/test_snapshot_cli.py -v`
Expected: FAIL — `No module named memory.snapshot.__main__` / non-zero from missing CLI

- [ ] **Step 3: Write minimal implementation**

Add to `memory-service/src/memory/snapshot.py`:

```python
def _git_head(repo_dir: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main() -> None:
    import sys

    if len(sys.argv) < 3 or sys.argv[1] not in ("dump", "restore"):
        print("usage: python -m memory.snapshot dump|restore <dir>", file=sys.stderr)
        raise SystemExit(2)

    action, target = sys.argv[1], sys.argv[2]
    settings = Settings.from_env()

    if action == "dump":
        dump(target, settings, source_head=_git_head(os.path.dirname(os.path.abspath(target))))
        print(f"dumped snapshot to {target}")
    else:
        ok = restore(target, settings)
        print("restored from snapshot" if ok else "no compatible snapshot; caller should seed")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd memory-service && uv run pytest tests/test_snapshot_cli.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/snapshot.py memory-service/tests/test_snapshot_cli.py
git commit -m "feat(snapshot): CLI dump/restore entrypoint with non-zero dump exit"
```

---

### Task 6: Writability probe + named-volume fallback

Decide where the snapshot lives at startup: prefer `PROJECT_DIR/.agent-memory`; on an unwritable target fall back to the named-volume mount and warn loudly. No data loss.

**Files:**
- Create: `memory-service/src/memory/startup.py`
- Test: `memory-service/tests/test_startup_home.py`

**Interfaces:**
- Produces: `snapshot_home(project_dir: str, fallback_dir: str = "/agent-memory") -> tuple[str, str]` — returns `(home_dir, location)` where `location` is `"co-located"` or `"fallback-volume"`. Probes by creating `<project_dir>/.agent-memory`, touching and removing a temp file inside it.

- [ ] **Step 1: Write the failing test**

```python
# memory-service/tests/test_startup_home.py
import os
import stat

from memory.startup import snapshot_home


def test_home_is_colocated_when_writable(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    fallback = tmp_path / "fallback"

    home, location = snapshot_home(str(project), fallback_dir=str(fallback))

    assert location == "co-located"
    assert home == str(project / ".agent-memory")
    assert os.path.isdir(home)


def test_home_falls_back_when_unwritable(tmp_path):
    project = tmp_path / "ro-project"
    project.mkdir()
    os.chmod(project, stat.S_IRUSR | stat.S_IXUSR)  # read-only: cannot mkdir inside
    fallback = tmp_path / "fallback"

    try:
        home, location = snapshot_home(str(project), fallback_dir=str(fallback))
    finally:
        os.chmod(project, stat.S_IRWXU)  # restore so tmp cleanup works

    assert location == "fallback-volume"
    assert home == str(fallback)
    assert os.path.isdir(home)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd memory-service && uv run pytest tests/test_startup_home.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'memory.startup'`

- [ ] **Step 3: Write minimal implementation**

```python
# memory-service/src/memory/startup.py
"""One-shot startup: enforce isolation (reset), pick the snapshot home (probe +
fallback), restore-or-seed, then catch up with an incremental reconcile."""
from __future__ import annotations

import os
import sys


def snapshot_home(project_dir: str, fallback_dir: str = "/agent-memory") -> tuple[str, str]:
    """Return (home_dir, location). Prefer PROJECT_DIR/.agent-memory; on an
    unwritable target fall back to the named-volume mount. Never lossy."""
    colocated = os.path.join(project_dir, ".agent-memory")
    try:
        os.makedirs(colocated, exist_ok=True)
        probe = os.path.join(colocated, ".write-probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return colocated, "co-located"
    except OSError:
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir, "fallback-volume"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd memory-service && uv run pytest tests/test_startup_home.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add memory-service/src/memory/startup.py memory-service/tests/test_startup_home.py
git commit -m "feat(startup): writability probe with named-volume fallback"
```

---

### Task 7: Startup orchestration (`python -m memory.startup /project`)

Wire the full startup sequence: probe home → reset DB (isolation) → restore-or-seed → always incremental `reconcile` (catch-up). Refactor `reconcile.main` to expose a reusable `run()` so startup does not duplicate setup.

**Files:**
- Modify: `memory-service/src/memory/reconcile.py` (extract `run(root, settings) -> dict`)
- Modify: `memory-service/src/memory/startup.py` (add `run_startup`, `main`, `__main__`)
- Test: `memory-service/tests/test_startup_flow.py`

> **Existing-code change.** `reconcile.py` is referenced in [docs/architecture.md](../../architecture.md) (Diagram 3 notes: "one-shot reconcile (full scan) to seed a project or recover from drift"). The new startup flow and its place in the data flow are documented in Task 9.

**Interfaces:**
- Consumes: `memory.config.Settings`, `memory.db.connect`/`reset_db`, `memory.snapshot.restore`, `memory.reconcile.run`, `snapshot_home` (Task 6).
- Produces:
  - `memory.reconcile.run(root: str, settings: Settings) -> dict` — connects, applies schema, ensures embedding config, reconciles `root`; returns the reconcile summary (`{"processed", "removed", "pruned_links"}`).
  - `memory.startup.run_startup(root: str, settings: Settings) -> dict` — performs probe → reset → restore-or-seed → catch-up; returns `{"location": str, "restored": bool, "reconcile": dict}`.
  - CLI `python -m memory.startup /project`.

- [ ] **Step 1: Refactor `reconcile.main` to delegate to `run()`**

Replace the bottom of `memory-service/src/memory/reconcile.py` (the `main` function and below) with:

```python
def run(root: str, settings: "Settings") -> dict:
    from memory.db import apply_schema, connect
    from memory.embeddings.factory import build_embedder

    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    repo.ensure_embedding_config("code", settings.code_embed.provider, settings.code_embed.model, settings.code_embed.dim)
    repo.ensure_embedding_config("doc", settings.doc_embed.provider, settings.doc_embed.model, settings.doc_embed.dim)
    worker = Worker(repo, build_embedder(settings.code_embed), build_embedder(settings.doc_embed))
    return reconcile(repo, worker, root)


def main() -> None:
    import sys

    from memory.config import Settings

    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(run(root, Settings.from_env()))


if __name__ == "__main__":
    main()
```

Add the import used by the type hint at the top of `reconcile.py` (alongside existing imports):

```python
from memory.config import Settings
```

- [ ] **Step 2: Write the failing test**

```python
# memory-service/tests/test_startup_flow.py
from memory.config import Settings
from memory.db import apply_schema, connect, reset_db
from memory import snapshot, startup


def _project_with_one_file(tmp_path):
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "mod.py").write_text("def hello():\n    return 1\n")
    return proj


def test_seed_path_when_no_snapshot(tmp_path):
    settings = Settings.from_env()
    conn = connect(settings); reset_db(conn); conn.close()
    proj = _project_with_one_file(tmp_path)

    result = startup.run_startup(str(proj), settings)

    assert result["restored"] is False
    assert result["location"] == "co-located"
    conn = connect(settings)
    files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    conn.close()
    assert files >= 1  # seeded from source


def test_restore_path_preserves_knowledge(tmp_path):
    settings = Settings.from_env()
    proj = _project_with_one_file(tmp_path)
    home = proj / ".agent-memory"
    home.mkdir()

    # build a DB with agent knowledge, then snapshot it into the project home
    conn = connect(settings); reset_db(conn); apply_schema(conn)
    conn.execute("INSERT INTO spec_links (spec_path, symbol_qualname) VALUES ('s.md','pkg.mod.hello')")
    conn.close()
    snapshot.dump(str(home), settings)

    # wipe live DB, then start up: should restore (not seed) and keep the link
    conn = connect(settings); reset_db(conn); conn.close()
    result = startup.run_startup(str(proj), settings)

    assert result["restored"] is True
    conn = connect(settings)
    links = conn.execute("SELECT count(*) FROM spec_links").fetchone()[0]
    conn.close()
    assert links == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd memory-service && uv run pytest tests/test_startup_flow.py -v`
Expected: FAIL — `AttributeError: module 'memory.startup' has no attribute 'run_startup'`

- [ ] **Step 4: Write minimal implementation**

Add to `memory-service/src/memory/startup.py` (and add the imports shown):

```python
from memory import reconcile, snapshot
from memory.config import Settings
from memory.db import connect, reset_db


def run_startup(root: str, settings: Settings) -> dict:
    home, location = snapshot_home(root)
    if location == "fallback-volume":
        print(
            "WARNING: PROJECT_DIR/.agent-memory is not writable; memory will NOT "
            "co-locate with the source. Persisting to the named volume instead.",
            file=sys.stderr,
        )

    # Isolation: the snapshot (or a fresh seed), not the previous volume, is the
    # source of truth on every start.
    conn = connect(settings)
    reset_db(conn)
    conn.close()

    restored = snapshot.restore(home, settings)

    # Always catch up. reconcile applies the schema + seeds when the DB is empty
    # (no restore), and re-embeds only changed files when restored (hash-diff).
    summary = reconcile.run(root, settings)
    return {"location": location, "restored": restored, "reconcile": summary}


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "/project"
    print(run_startup(root, Settings.from_env()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run reconcile + startup tests to verify they pass**

Run: `cd memory-service && uv run pytest tests/test_reconcile.py tests/test_startup_flow.py -v`
Expected: PASS (existing reconcile tests still green after the `run()` refactor; both startup-flow tests pass)

- [ ] **Step 6: Commit**

```bash
git add memory-service/src/memory/reconcile.py memory-service/src/memory/startup.py memory-service/tests/test_startup_flow.py
git commit -m "feat(startup): orchestrate reset, restore-or-seed, and catch-up reconcile"
```

---

### Task 8: Dockerfile (pg client 16) + compose wiring (init service, volume, mounts)

Make `pg_dump`/`pg_restore` available in the image and add the one-shot `init` service plus the unwritable-fallback volume.

**Files:**
- Modify: `memory-service/Dockerfile` (install `postgresql-client-16`)
- Modify: `memory-service/docker-compose.yml` (add `init` service, `agent-memory` volume, gate `worker` **and** `memory` on init)
- Modify: `docker-compose.yml` (root) — gate `agent-worker` on init completing

> **Existing-code change.** Both the image build (architecture.md Diagram 2) and runtime topology (Diagram 1, the volumes and service set) change. Documented in Task 9.

**Interfaces:**
- Consumes: `memory.startup` (Task 7) as the `init` service command.
- Produces: a memory image with `pg_dump`/`pg_restore` 16; an `init` one-shot that runs `python -m memory.startup /project`; a named volume `agent-memory`; `worker` now depends on `init` completing.

- [ ] **Step 1: Add the PostgreSQL 16 client to the Dockerfile**

Insert into `memory-service/Dockerfile` immediately after `RUN pip install --no-cache-dir uv`:

```dockerfile
# pg_dump / pg_restore (major 16, matching the pgvector:pg16 server) for snapshots
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/*
```

- [ ] **Step 2: Add the `init` service and `agent-memory` volume to compose**

In `memory-service/docker-compose.yml`, add this service (mirror the `worker` env block exactly) before the `worker` service:

```yaml
  init:
    build: .
    depends_on:
      db:
        condition: service_healthy
      embeddings:
        condition: service_healthy
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
    volumes:
      # read-write so startup can create PROJECT_DIR/.agent-memory next to the source
      - ${PROJECT_DIR:-./}:/project
      - agent-memory:/agent-memory   # unwritable-fallback snapshot home
    command: ["uv", "run", "python", "-m", "memory.startup", "/project"]
```

- [ ] **Step 3: Gate every DB consumer on init completing**

`init` resets the DB (`DROP SCHEMA public`) on startup, so **every** service that reads/writes the DB must wait for it to finish — otherwise an agent can query the DB mid-reset/restore. Add `init: { condition: service_completed_successfully }` to the `depends_on` of:

1. the `worker` service in `memory-service/docker-compose.yml`,
2. the `memory` service in `memory-service/docker-compose.yml`,
3. the `agent-worker` service in the **root** `docker-compose.yml` (the root file `include`s the memory-service compose, so `init` is referenceable in the same project).

```yaml
# memory-service/docker-compose.yml — worker AND memory each gain:
    depends_on:
      db:
        condition: service_healthy
      embeddings:
        condition: service_healthy
      init:
        condition: service_completed_successfully
```

```yaml
# docker-compose.yml (root) — agent-worker:
  agent-worker:
    ...
    depends_on:
      db:
        condition: service_healthy
      embeddings:
        condition: service_healthy
      init:
        condition: service_completed_successfully
```

- [ ] **Step 4: Add the `agent-memory` named volume**

Change the `volumes:` block at the bottom of `memory-service/docker-compose.yml` from:

```yaml
volumes:
  pgdata:
```

to:

```yaml
volumes:
  pgdata:
  agent-memory:
```

- [ ] **Step 5: Verify the image builds and the init service runs end-to-end**

Run: `cd memory-service && docker compose build init && PROJECT_DIR=$(pwd) docker compose run --rm init`
Expected: build succeeds; the run prints a dict like `{'location': 'co-located', 'restored': False, 'reconcile': {...}}`, and a `.agent-memory/` (with `.gitignore` containing `*`) appears under `memory-service/`.

- [ ] **Step 6: Commit**

```bash
git add memory-service/Dockerfile memory-service/docker-compose.yml docker-compose.yml
git commit -m "feat(compose): add init startup service, pg16 client, fallback volume; gate consumers on init"
```

---

### Task 9: Update architecture & spec docs

Document the new startup/snapshot flow and changed topology, and mark the spec implemented. The user explicitly requested doc updates for every change to existing behavior.

**Files:**
- Modify: `docs/architecture.md`
- Modify: `docs/superpowers/specs/2026-06-25-per-project-snapshot-memory-design.md` (status only)

**Interfaces:**
- Consumes: the behavior built in Tasks 1–8.
- Produces: architecture doc reflecting per-project snapshots; spec marked implemented.

- [ ] **Step 1: Add a snapshot/startup section to architecture.md**

In `docs/architecture.md`, after "Diagram 3 — Data flow A: keeping memory fresh (ingestion)", add a new subsection (place before "Diagram 4"):

```markdown
## Diagram 3b — Startup: per-project snapshot restore-or-seed

Memory is **per-project**, co-located with the source under a self-ignoring
`PROJECT_DIR/.agent-memory/` (a `.gitignore` of `*`), and snapshot-able. A
one-shot `init` service runs before the steady worker:

\```mermaid
flowchart TB
    UP["docker compose up"] --> RESET["reset live DB<br/>(DROP SCHEMA public)"]
    RESET --> PROBE["probe .agent-memory writable?"]
    PROBE -->|"no"| FB["fall back to named volume<br/>agent-memory + warn"]
    PROBE -->|"yes"| HOME["home = PROJECT_DIR/.agent-memory"]
    FB --> DECIDE
    HOME --> DECIDE{"snapshot.dump present<br/>AND meta.json compatible?"}
    DECIDE -->|"yes"| RESTORE["pg_restore (graph + embeddings + spec_links)"]
    DECIDE -->|"no"| SEED["create tables + reconcile (seed)"]
    RESTORE --> CATCH["incremental reconcile (catch up)"]
    SEED --> CATCH
\```

- **Isolation:** the live Postgres volume is reset on every start, so the
  on-disk snapshot — not whatever the global volume held — is the source of
  truth. Point `PROJECT_DIR` at a different repo and you get that repo's memory.
- **Derivable vs irreplaceable:** the structure graph + chunks + embeddings are
  rebuildable by `reconcile`; agent-authored `spec_links` (`add_knowledge`) are
  not, so the snapshot protects them. A corrupt/incompatible snapshot degrades
  to a fresh seed — working-but-loud, never working-but-lossy.
- **Explicit save:** `python -m memory.snapshot dump PROJECT_DIR/.agent-memory`
  (atomic; exits non-zero on failure) is called before teardown. The
  compatibility guard (`meta.json`: schema version, pg major, code/doc model+dim)
  refuses a restore that would mix embedding dimensions.
\```
```

(Remove the stray closing fence if your editor adds one; the block above is one section.)

- [ ] **Step 2: Update the runtime-topology and image notes in architecture.md**

In `docs/architecture.md`, under Diagram 1's "How to read it" list, append a bullet:

```markdown
- **Per-project memory:** a one-shot `init` service (same memory image) runs
  `python -m memory.startup /project` before the worker, restoring or seeding
  the DB from `PROJECT_DIR/.agent-memory/snapshot.dump`. An `agent-memory`
  named volume is the fallback when `.agent-memory/` is not writable.
```

And under Diagram 2's running-services list, add `init` alongside the other commands so the "same image, different command" set includes `init → runs startup`.

- [ ] **Step 3: Mark the spec implemented**

In `docs/superpowers/specs/2026-06-25-per-project-snapshot-memory-design.md`, change the header status line from:

```markdown
- **Status:** Approved (ready for implementation plan)
```

to:

```markdown
- **Status:** Implemented — see [plan](../plans/2026-06-26-per-project-snapshot-memory.md)
```

> The spec's `knowledge`→`spec_links` wording and the provider-tolerance / init-gating notes were already corrected during plan review — only the status line remains for this step.

- [ ] **Step 4: Verify the mermaid renders and links resolve**

Run: `cd "/Users/hirenppp/Documents/Claude Escapades/agent_image" && grep -n "memory.startup\|agent-memory\|Diagram 3b" docs/architecture.md`
Expected: matches in the new section confirming the edits landed.

- [ ] **Step 5: Commit**

```bash
git add docs/architecture.md docs/superpowers/specs/2026-06-25-per-project-snapshot-memory-design.md
git commit -m "docs: document per-project snapshot startup flow; mark spec implemented"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `cd memory-service && docker compose up -d db && uv run pytest -v`
Expected: all tests pass, including the new `test_snapshot_meta`, `test_reset_db`, `test_snapshot_dump`, `test_snapshot_restore`, `test_snapshot_cli`, `test_startup_home`, `test_startup_flow`. (Dump/restore tests require pg client 16 on PATH — see Test prerequisites.)
