# Memory Service (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the persistent knowledge layer — a Postgres/pgvector service that ingests a repo into a structure graph (with **resolved** symbol→symbol edges), semantic embeddings, and spec↔code linkage, kept fresh by a decoupled enqueue→worker→reconcile pipeline, and exposed over MCP. The **embedding model runs as an external service** (default self-hosted TEI), selectable per collection (code vs docs); our images stay model-free.

**Architecture:** A Python service over one Postgres instance with `pgvector`. A git post-commit hook only **enqueues** changed files into a Postgres `ingest_queue` table and returns immediately. A **worker** drains the queue: parse (tree-sitter) → chunk on AST boundaries (with structural breadcrumb) → diff by per-chunk content hash → batch-embed only changed chunks (via a per-collection `EmbeddingProvider`) → upsert structure + vectors + linkage → re-resolve the affected edges (reverse-dependency **closure**). A periodic **reconcile** job does a full re-scan, re-resolves all edges, and prunes dangling spec links. Embedding is pluggable: `LocalEmbeddingProvider` (in-process `fastembed`, the offline/test default) or `RemoteEmbeddingProvider` (HTTP to a TEI server, the running-stack default), chosen independently for code and docs via config. Per-collection embedding dimension + model identity are recorded in `embedding_config`, and a mismatch is refused rather than silently mixed.

**Tech Stack:** Python 3.12, `uv`, Postgres 16 + `pgvector`, `psycopg` 3 + `pgvector.psycopg`, `tree-sitter` + `tree-sitter-python`, `fastembed` (local default), `httpx` (TEI client), Hugging Face **TEI** (Text Embeddings Inference) as the default external embedder, `mcp`, `pytest` + `anyio`.

## Global Constraints

- Python `>=3.12`; package manager `uv` only (never `pip`).
- Postgres 16; `pgvector` `>=0.7`.
- **Embeddings are external and pluggable per collection.** Provider is `local` (in-process `fastembed`, default for tests/offline) or `remote` (HTTP to TEI, default for the running stack), chosen independently for `code` and `doc` via env (`CODE_EMBED_*`, `DOC_EMBED_*`). Models are **not** baked into any image.
- Each collection has its own fixed pgvector dimension, set at schema init from config (`CODE_EMBED_DIM`, `DOC_EMBED_DIM`; default **384**). The `(collection → provider, model, dim)` triple is recorded in `embedding_config`; ingest **refuses** on mismatch (raise `EmbeddingConfigMismatch`) — swapping a model requires a reconcile/re-embed.
- The git hook **enqueues only**; embedding never runs inside the hook.
- Platform target `linux/arm64` (Apple Silicon). NOTE: verify the TEI image tag supports arm64 CPU; tests use the `local` provider and never require a running TEI.
- Pinned floors in `pyproject.toml`: `psycopg[binary]>=3.2`, `pgvector>=0.3.6`, `tree-sitter>=0.23`, `tree-sitter-python>=0.23`, `fastembed>=0.4`, `httpx>=0.27`, `mcp>=1.2`, `pytest>=8.3`, `anyio>=4.4`, `trio>=0.26`.
- All work in the `memory-service/` subdirectory. TDD: failing test → verify fail → minimal impl → verify pass → commit.

---

### Task 1: Scaffold, config, db, and schema

**Files:**
- Create: `memory-service/pyproject.toml`, `memory-service/docker-compose.yml`, `memory-service/sql/001_schema.sql`
- Create: `memory-service/src/memory/__init__.py`, `config.py`, `db.py`
- Test: `memory-service/tests/conftest.py`, `memory-service/tests/test_schema.py`

**Interfaces:**
- Produces:
  - `memory.config.EmbedConfig(provider: str, model: str, dim: int, url: str | None)` (frozen).
  - `memory.config.Settings(database_url: str, code_embed: EmbedConfig, doc_embed: EmbedConfig)` + `Settings.from_env()`.
  - `memory.db.connect(settings) -> psycopg.Connection` (autocommit, vector registered).
  - `memory.db.apply_schema(conn, code_dim: int = 384, doc_dim: int = 384) -> None` (idempotent; templates the vector dimensions).
  - Tables: `files`, `symbols`, `edges`, `code_chunks`, `doc_chunks`, `spec_links`, `ingest_queue`, `embedding_config`.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "memory-service"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "psycopg[binary]>=3.2",
    "pgvector>=0.3.6",
    "tree-sitter>=0.23",
    "tree-sitter-python>=0.23",
    "fastembed>=0.4",
    "httpx>=0.27",
    "mcp>=1.2",
]

[project.optional-dependencies]
dev = ["pytest>=8.3", "anyio>=4.4", "trio>=0.26"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/memory"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]
```

- [ ] **Step 2: Create `docker-compose.yml`** (db only for now; `embeddings`/`memory`/`worker` added in Task 12)

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: memory
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 2s
      timeout: 3s
      retries: 20

volumes:
  pgdata:
```

- [ ] **Step 3: Create `sql/001_schema.sql`** (`:CODE_DIM`/`:DOC_DIM` are templated by `apply_schema`)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS files (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    path         TEXT UNIQUE NOT NULL,
    language     TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS symbols (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id    BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    qualname   TEXT NOT NULL,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    start_line INT NOT NULL,
    end_line   INT NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id       BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    src_qualname  TEXT NOT NULL,
    dst_name      TEXT NOT NULL,
    dst_symbol_id BIGINT REFERENCES symbols(id) ON DELETE SET NULL,
    kind          TEXT NOT NULL,
    resolution    TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS code_chunks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    file_id      BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    chunk_key    TEXT NOT NULL,
    qualname     TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(:CODE_DIM) NOT NULL,
    UNIQUE (file_id, chunk_key)
);

CREATE TABLE IF NOT EXISTS doc_chunks (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    path         TEXT NOT NULL,
    chunk_key    TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(:DOC_DIM) NOT NULL,
    UNIQUE (path, chunk_key)
);

CREATE TABLE IF NOT EXISTS spec_links (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    spec_path       TEXT NOT NULL,
    symbol_qualname TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_queue (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    commit_sha  TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    enqueued_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embedding_config (
    collection TEXT PRIMARY KEY,
    provider   TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INT  NOT NULL
);

CREATE INDEX IF NOT EXISTS code_chunks_embedding_idx ON code_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS doc_chunks_embedding_idx ON doc_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS symbols_name_idx ON symbols (name);
CREATE INDEX IF NOT EXISTS symbols_qualname_idx ON symbols (qualname);
CREATE INDEX IF NOT EXISTS edges_dst_name_idx ON edges (dst_name);
CREATE INDEX IF NOT EXISTS edges_dst_symbol_idx ON edges (dst_symbol_id);
CREATE INDEX IF NOT EXISTS ingest_queue_status_idx ON ingest_queue (status);
```

- [ ] **Step 4: Create `src/memory/__init__.py` (empty), `config.py`, `db.py`**

`config.py`:

```python
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbedConfig:
    provider: str       # "local" | "remote"
    model: str
    dim: int
    url: str | None


@dataclass(frozen=True)
class Settings:
    database_url: str
    code_embed: EmbedConfig
    doc_embed: EmbedConfig

    @classmethod
    def from_env(cls) -> "Settings":
        def embed(prefix: str) -> EmbedConfig:
            return EmbedConfig(
                provider=os.environ.get(f"{prefix}_EMBED_PROVIDER", "local"),
                model=os.environ.get(f"{prefix}_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
                dim=int(os.environ.get(f"{prefix}_EMBED_DIM", "384")),
                url=os.environ.get(f"{prefix}_EMBED_URL"),
            )

        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/memory"
            ),
            code_embed=embed("CODE"),
            doc_embed=embed("DOC"),
        )
```

`db.py`:

```python
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from memory.config import Settings

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "sql" / "001_schema.sql"


def connect(settings: Settings) -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def apply_schema(conn: psycopg.Connection, code_dim: int = 384, doc_dim: int = 384) -> None:
    sql = SCHEMA_FILE.read_text()
    sql = sql.replace(":CODE_DIM", str(code_dim)).replace(":DOC_DIM", str(doc_dim))
    conn.execute(sql)
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
import pytest

from memory.config import Settings
from memory.db import apply_schema, connect

TABLES = [
    "files", "symbols", "edges", "code_chunks", "doc_chunks",
    "spec_links", "ingest_queue", "embedding_config",
]


@pytest.fixture()
def conn():
    connection = connect(Settings.from_env())
    apply_schema(connection)  # default 384/384 for tests
    for table in TABLES:
        connection.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")
    yield connection
    connection.close()


@pytest.fixture()
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 6: Write `tests/test_schema.py`**

```python
def test_all_tables_exist(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {
        "files", "symbols", "edges", "code_chunks", "doc_chunks",
        "spec_links", "ingest_queue", "embedding_config",
    }.issubset(names)


def test_edges_have_resolution_columns(conn):
    cols = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='edges'"
        ).fetchall()
    }
    assert {"dst_symbol_id", "resolution", "dst_name"}.issubset(cols)
```

- [ ] **Step 7: Start Postgres, install deps, run tests (expect PASS)**

```bash
cd memory-service
docker compose up -d db
uv sync --extra dev
uv run pytest tests/test_schema.py -v
```
Expected: PASS. (A SQL typo FAILs here; fix and rerun.)

- [ ] **Step 8: Commit** — `git commit -m "feat(memory): scaffold + templated-dim schema + embedding_config + queue"`

---

### Task 2: Embedding providers (local + remote/TEI) and factory

**Files:**
- Create: `memory-service/src/memory/embeddings/__init__.py`, `base.py`, `local.py`, `remote.py`, `factory.py`
- Test: `memory-service/tests/test_embeddings.py`

**Interfaces:**
- Produces:
  - `memory.embeddings.base.EmbeddingProvider` (Protocol: `dim: int`, `embed(texts: list[str]) -> list[list[float]]`).
  - `memory.embeddings.local.LocalEmbeddingProvider(model_name="BAAI/bge-small-en-v1.5")` (`fastembed`; `dim==384` for the default model).
  - `memory.embeddings.remote.RemoteEmbeddingProvider(base_url: str, dim: int, timeout: float = 60.0)` — POSTs `{"inputs": texts}` to `{base_url}/embed` (TEI), returns the JSON list of vectors.
  - `memory.embeddings.factory.build_embedder(cfg: EmbedConfig) -> EmbeddingProvider` — `remote` → `RemoteEmbeddingProvider(cfg.url, cfg.dim)` (raises if `cfg.url` missing); else `LocalEmbeddingProvider(cfg.model)`.

- [ ] **Step 1: Write `tests/test_embeddings.py`**

```python
import pytest

from memory.config import EmbedConfig
from memory.embeddings import remote as remote_mod
from memory.embeddings.factory import build_embedder
from memory.embeddings.local import LocalEmbeddingProvider
from memory.embeddings.remote import RemoteEmbeddingProvider


def test_local_dim_and_embed():
    p = LocalEmbeddingProvider()
    assert p.dim == 384
    vectors = p.embed(["def foo(): pass", "a paragraph"])
    assert len(vectors) == 2 and all(len(v) == 384 for v in vectors)
    assert p.embed([]) == []


def test_remote_posts_to_tei(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return [[0.0] * 384]

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr(remote_mod.httpx, "post", fake_post)
    p = RemoteEmbeddingProvider("http://embeddings:80", dim=384)
    assert p.dim == 384
    assert p.embed([]) == []
    assert p.embed(["hi"]) == [[0.0] * 384]
    assert captured["url"] == "http://embeddings:80/embed"
    assert captured["json"] == {"inputs": ["hi"]}


def test_factory_selects_provider():
    assert isinstance(
        build_embedder(EmbedConfig("local", "BAAI/bge-small-en-v1.5", 384, None)),
        LocalEmbeddingProvider,
    )
    assert isinstance(
        build_embedder(EmbedConfig("remote", "x", 384, "http://embeddings:80")),
        RemoteEmbeddingProvider,
    )
    with pytest.raises(ValueError):
        build_embedder(EmbedConfig("remote", "x", 384, None))
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.embeddings'`.

- [ ] **Step 3: Create `src/memory/embeddings/__init__.py` (empty), `base.py`, `local.py`, `remote.py`, `factory.py`**

`base.py`:

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def dim(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
```

`local.py`:

```python
from fastembed import TextEmbedding

_MODEL_DIMS = {"BAAI/bge-small-en-v1.5": 384}


class LocalEmbeddingProvider:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        self._model = TextEmbedding(model_name=model_name)
        self._dim = _MODEL_DIMS.get(model_name, len(next(iter(self._model.embed(["probe"])))))

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vector.tolist() for vector in self._model.embed(texts)]
```

`remote.py`:

```python
import httpx


class RemoteEmbeddingProvider:
    def __init__(self, base_url: str, dim: int, timeout: float = 60.0) -> None:
        self._url = base_url.rstrip("/")
        self._dim = dim
        self._timeout = timeout

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = httpx.post(f"{self._url}/embed", json={"inputs": texts}, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()
```

`factory.py`:

```python
from memory.config import EmbedConfig
from memory.embeddings.base import EmbeddingProvider
from memory.embeddings.local import LocalEmbeddingProvider
from memory.embeddings.remote import RemoteEmbeddingProvider


def build_embedder(cfg: EmbedConfig) -> EmbeddingProvider:
    if cfg.provider == "remote":
        if not cfg.url:
            raise ValueError("remote embedder requires a *_EMBED_URL")
        return RemoteEmbeddingProvider(cfg.url, cfg.dim)
    return LocalEmbeddingProvider(cfg.model)
```

- [ ] **Step 4: Run test, verify PASS** (first run downloads the local model).

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): pluggable embeddings (local fastembed + remote TEI) + factory"`

---

### Task 3: Python source parser (structure layer)

**Files:**
- Create: `memory-service/src/memory/models.py`, `parser/__init__.py`, `parser/base.py`, `parser/python_parser.py`
- Test: `memory-service/tests/test_python_parser.py`

**Interfaces:**
- Produces: `memory.models.ParsedSymbol(qualname, name, kind, start_line, end_line)`; `ParsedEdge(src_qualname, dst_name, kind)`; `ParsedFile(path, language, source, symbols, edges)` (all frozen); `memory.parser.base.LanguageParser` (Protocol `parse(path, source) -> ParsedFile`); `memory.parser.python_parser.PythonParser()`.

- [ ] **Step 1: Write `tests/test_python_parser.py`**

```python
from memory.parser.python_parser import PythonParser

SAMPLE = '''import os

def helper():
    return os.getcwd()

class Service:
    def run(self):
        return helper()
'''


def test_symbols():
    parsed = PythonParser().parse("svc.py", SAMPLE)
    kinds = {s.qualname: s.kind for s in parsed.symbols}
    assert kinds["helper"] == "function"
    assert kinds["Service"] == "class"
    assert kinds["Service.run"] == "method"


def test_edges():
    parsed = PythonParser().parse("svc.py", SAMPLE)
    calls = {(e.src_qualname, e.dst_name) for e in parsed.edges if e.kind == "calls"}
    imports = {e.dst_name for e in parsed.edges if e.kind == "imports"}
    assert ("Service.run", "helper") in calls
    assert "os" in imports
    assert parsed.language == "python" and parsed.path == "svc.py"
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.parser'`.

- [ ] **Step 3: Create `src/memory/models.py`**

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSymbol:
    qualname: str
    name: str
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ParsedEdge:
    src_qualname: str
    dst_name: str
    kind: str


@dataclass(frozen=True)
class ParsedFile:
    path: str
    language: str
    source: str
    symbols: list[ParsedSymbol]
    edges: list[ParsedEdge]
```

- [ ] **Step 4: Create `src/memory/parser/__init__.py` (empty) and `parser/base.py`**

```python
from typing import Protocol

from memory.models import ParsedFile


class LanguageParser(Protocol):
    def parse(self, path: str, source: str) -> ParsedFile: ...
```

- [ ] **Step 5: Create `src/memory/parser/python_parser.py`**

```python
import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from memory.models import ParsedEdge, ParsedFile, ParsedSymbol

_PY_LANGUAGE = Language(tspython.language())


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf8")


def _name_of(node: Node, src: bytes) -> str:
    n = node.child_by_field_name("name")
    return _text(n, src) if n else "<anonymous>"


def _callee_name(call: Node, src: bytes) -> str:
    fn = call.child_by_field_name("function")
    if fn is None:
        return ""
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        return _text(attr, src) if attr else ""
    return _text(fn, src)


class PythonParser:
    def __init__(self) -> None:
        self._parser = Parser(_PY_LANGUAGE)

    def parse(self, path: str, source: str) -> ParsedFile:
        src = source.encode("utf8")
        tree = self._parser.parse(src)
        symbols: list[ParsedSymbol] = []
        edges: list[ParsedEdge] = []

        def collect_calls(def_node: Node, owner: str) -> None:
            stack = list(def_node.children)
            while stack:
                n = stack.pop()
                if n.type in ("function_definition", "class_definition"):
                    continue
                if n.type == "call":
                    callee = _callee_name(n, src)
                    if callee:
                        edges.append(ParsedEdge(owner, callee, "calls"))
                stack.extend(n.children)

        def collect_imports(node: Node) -> None:
            for n in node.children:
                if n.type == "dotted_name":
                    edges.append(ParsedEdge("<module>", _text(n, src).split(".")[0], "imports"))

        def visit(node: Node, scope: str) -> None:
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    name = _name_of(child, src)
                    qualname = f"{scope}.{name}" if scope else name
                    kind = "class" if child.type == "class_definition" else ("method" if scope else "function")
                    symbols.append(ParsedSymbol(qualname, name, kind, child.start_point[0] + 1, child.end_point[0] + 1))
                    if child.type == "function_definition":
                        collect_calls(child, qualname)
                    visit(child, qualname)
                elif child.type in ("import_statement", "import_from_statement"):
                    collect_imports(child)
                else:
                    visit(child, scope)

        visit(tree.root_node, "")
        return ParsedFile(path, "python", source, symbols, edges)
```

- [ ] **Step 6: Run test, verify PASS.**

- [ ] **Step 7: Commit** — `git commit -m "feat(memory): tree-sitter python parser (symbols + call/import edges)"`

---

### Task 4: AST-boundary chunking (breadcrumb + oversized split + content hash)

**Files:**
- Create: `memory-service/src/memory/chunking.py`
- Test: `memory-service/tests/test_chunking.py`

**Interfaces:**
- Consumes: `ParsedFile` (Task 3).
- Produces:
  - `memory.chunking.CodeChunk(chunk_key, qualname, content_hash, text)` (frozen).
  - `memory.chunking.DocChunk(chunk_key, path, content_hash, text)` (frozen).
  - `memory.chunking.normalize(text) -> str` (rstrip each line, drop blank lines).
  - `memory.chunking.chunk_code(parsed, max_lines=80) -> list[CodeChunk]` — one chunk per `function`/`method` (classes skipped); text = `# {path} > {qualname}` breadcrumb + symbol source; oversized symbols split on blank-line blocks; `content_hash = sha256(normalize(body))`; `chunk_key = f"{qualname}#{i}"`.
  - `memory.chunking.chunk_docs(path, text) -> list[DocChunk]` — split on blank lines; `chunk_key = f"{path}#{i}"`.

- [ ] **Step 1: Write `tests/test_chunking.py`**

```python
from memory.chunking import chunk_code, chunk_docs, normalize
from memory.models import ParsedFile, ParsedSymbol

SOURCE = "def foo():\n    return 1\n\nclass C:\n    def m(self):\n        return 2\n"


def test_chunk_code_skips_classes_and_breadcrumbs():
    parsed = ParsedFile(
        "m.py", "python", SOURCE,
        symbols=[
            ParsedSymbol("foo", "foo", "function", 1, 2),
            ParsedSymbol("C", "C", "class", 4, 6),
            ParsedSymbol("C.m", "m", "method", 5, 6),
        ],
        edges=[],
    )
    chunks = chunk_code(parsed)
    assert {c.chunk_key for c in chunks} == {"foo#0", "C.m#0"}
    foo = next(c for c in chunks if c.chunk_key == "foo#0")
    assert foo.text.startswith("# m.py > foo\n") and "return 1" in foo.text


def test_content_hash_ignores_trailing_whitespace():
    a = ParsedFile("m.py", "python", "def f():\n    return 1\n",
                   symbols=[ParsedSymbol("f", "f", "function", 1, 2)], edges=[])
    b = ParsedFile("m.py", "python", "def f():   \n    return 1  \n",
                   symbols=[ParsedSymbol("f", "f", "function", 1, 2)], edges=[])
    assert chunk_code(a)[0].content_hash == chunk_code(b)[0].content_hash


def test_oversized_function_splits():
    body = "def big():\n" + "\n".join(f"    a{i} = {i}" for i in range(50)) + "\n\n" + \
           "\n".join(f"    b{i} = {i}" for i in range(50)) + "\n"
    parsed = ParsedFile("m.py", "python", body,
                        symbols=[ParsedSymbol("big", "big", "function", 1, 103)], edges=[])
    assert {c.chunk_key for c in chunk_code(parsed, max_lines=80)} == {"big#0", "big#1"}


def test_chunk_docs():
    chunks = chunk_docs("r.md", "# T\n\nPara one.\n\nPara two.\n")
    assert [c.chunk_key for c in chunks] == ["r.md#0", "r.md#1", "r.md#2"]
    assert chunks[1].text == "Para one."
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.chunking'`.

- [ ] **Step 3: Create `src/memory/chunking.py`**

```python
import hashlib
from dataclasses import dataclass

from memory.models import ParsedFile


@dataclass(frozen=True)
class CodeChunk:
    chunk_key: str
    qualname: str
    content_hash: str
    text: str


@dataclass(frozen=True)
class DocChunk:
    chunk_key: str
    path: str
    content_hash: str
    text: str


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def _hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf8")).hexdigest()


def _split_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "" and current:
            blocks.append(current)
            current = []
        elif line.strip():
            current.append(line)
    if current:
        blocks.append(current)
    return blocks or [lines]


def chunk_code(parsed: ParsedFile, max_lines: int = 80) -> list[CodeChunk]:
    src_lines = parsed.source.splitlines()
    out: list[CodeChunk] = []
    for s in parsed.symbols:
        if s.kind not in ("function", "method"):
            continue
        body_lines = src_lines[s.start_line - 1 : s.end_line]
        breadcrumb = f"# {parsed.path} > {s.qualname}"
        parts = [body_lines] if len(body_lines) <= max_lines else _split_blocks(body_lines)
        for i, part in enumerate(parts):
            body = "\n".join(part)
            out.append(CodeChunk(f"{s.qualname}#{i}", s.qualname, _hash(body), f"{breadcrumb}\n{body}"))
    return out


def chunk_docs(path: str, text: str) -> list[DocChunk]:
    out: list[DocChunk] = []
    i = 0
    for block in (b.strip() for b in text.split("\n\n")):
        if not block:
            continue
        out.append(DocChunk(f"{path}#{i}", path, _hash(block), block))
        i += 1
    return out
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): AST-boundary chunking with breadcrumb + content hash"`

---

### Task 5: Repository — file + structure upsert

**Files:**
- Create: `memory-service/src/memory/repository.py`
- Test: `memory-service/tests/test_repository_structure.py`

**Interfaces:**
- Produces: `memory.repository.Repository(conn)` with `upsert_file_row(path, language, content_hash) -> int` (stable id via `ON CONFLICT (path)`); `file_hash(path) -> str | None`; `delete_file(path)`; `replace_structure(file_id, parsed)` (delete this file's symbols+edges, insert symbols, insert edges `resolution='pending'`); `list_db_files() -> list[str]`.

- [ ] **Step 1: Write `tests/test_repository_structure.py`**

```python
from memory.models import ParsedEdge, ParsedFile, ParsedSymbol
from memory.repository import Repository


def _parsed():
    return ParsedFile("svc.py", "python", "x",
                      symbols=[ParsedSymbol("helper", "helper", "function", 1, 2)],
                      edges=[ParsedEdge("helper", "print", "calls")])


def test_upsert_file_row_is_stable(conn):
    repo = Repository(conn)
    assert repo.upsert_file_row("svc.py", "python", "h1") == repo.upsert_file_row("svc.py", "python", "h2")
    assert repo.file_hash("svc.py") == "h2"


def test_replace_structure(conn):
    repo = Repository(conn)
    fid = repo.upsert_file_row("svc.py", "python", "h1")
    repo.replace_structure(fid, _parsed())
    repo.replace_structure(fid, _parsed())  # replaces, not appends
    assert conn.execute("SELECT count(*) FROM symbols").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM edges").fetchone()[0] == 1
    assert conn.execute("SELECT resolution FROM edges LIMIT 1").fetchone()[0] == "pending"


def test_delete_and_list(conn):
    repo = Repository(conn)
    fid = repo.upsert_file_row("svc.py", "python", "h1")
    repo.replace_structure(fid, _parsed())
    assert repo.list_db_files() == ["svc.py"]
    repo.delete_file("svc.py")
    assert repo.list_db_files() == []
    assert conn.execute("SELECT count(*) FROM symbols").fetchone()[0] == 0
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.repository'`.

- [ ] **Step 3: Create `src/memory/repository.py`**

```python
import psycopg

from memory.models import ParsedFile


class EmbeddingConfigMismatch(Exception):
    pass


class Repository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert_file_row(self, path: str, language: str, content_hash: str) -> int:
        return self._conn.execute(
            "INSERT INTO files (path, language, content_hash) VALUES (%s, %s, %s) "
            "ON CONFLICT (path) DO UPDATE SET language = EXCLUDED.language, "
            "content_hash = EXCLUDED.content_hash RETURNING id",
            (path, language, content_hash),
        ).fetchone()[0]

    def file_hash(self, path: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM files WHERE path = %s", (path,)
        ).fetchone()
        return row[0] if row else None

    def delete_file(self, path: str) -> None:
        self._conn.execute("DELETE FROM files WHERE path = %s", (path,))

    def replace_structure(self, file_id: int, parsed: ParsedFile) -> None:
        self._conn.execute("DELETE FROM symbols WHERE file_id = %s", (file_id,))
        self._conn.execute("DELETE FROM edges WHERE file_id = %s", (file_id,))
        for s in parsed.symbols:
            self._conn.execute(
                "INSERT INTO symbols (file_id, qualname, name, kind, start_line, end_line) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (file_id, s.qualname, s.name, s.kind, s.start_line, s.end_line),
            )
        for e in parsed.edges:
            self._conn.execute(
                "INSERT INTO edges (file_id, src_qualname, dst_name, kind, resolution) "
                "VALUES (%s, %s, %s, %s, 'pending')",
                (file_id, e.src_qualname, e.dst_name, e.kind),
            )

    def list_db_files(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT path FROM files ORDER BY path").fetchall()]
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): repository file + structure upsert (stable file id)"`

---

### Task 6: Edge resolution (resolved edges + closure)

**Files:**
- Modify: `memory-service/src/memory/repository.py` (append)
- Test: `memory-service/tests/test_resolution.py`

**Interfaces:**
- Produces (new methods): `resolve_pending_edges()`; `reresolve_all_edges()`; `impact_of(qualname) -> list[dict]` (`{"src_qualname","path"}`, via resolved edges). Resolution: one symbol named `dst_name` → `resolved`; many → `ambiguous`; none → `external`.

- [ ] **Step 1: Write `tests/test_resolution.py`**

```python
from memory.models import ParsedEdge, ParsedFile, ParsedSymbol
from memory.repository import Repository


def _ingest(repo, path, symbols, edges):
    fid = repo.upsert_file_row(path, "python", "h")
    repo.replace_structure(fid, ParsedFile(path, "python", "x", symbols, edges))


def test_resolves_cross_file_call(conn):
    repo = Repository(conn)
    _ingest(repo, "payment.py", [ParsedSymbol("process", "process", "function", 1, 2)], [])
    _ingest(repo, "checkout.py", [ParsedSymbol("order", "order", "function", 1, 2)],
            [ParsedEdge("order", "process", "calls")])
    repo.resolve_pending_edges()
    assert {c["src_qualname"] for c in repo.impact_of("process")} == {"order"}


def test_ambiguous_when_two_targets(conn):
    repo = Repository(conn)
    _ingest(repo, "a.py", [ParsedSymbol("run", "run", "function", 1, 2)], [])
    _ingest(repo, "b.py", [ParsedSymbol("run", "run", "function", 1, 2)], [])
    _ingest(repo, "c.py", [ParsedSymbol("main", "main", "function", 1, 2)],
            [ParsedEdge("main", "run", "calls")])
    repo.resolve_pending_edges()
    assert conn.execute("SELECT resolution FROM edges WHERE dst_name='run'").fetchone()[0] == "ambiguous"


def test_closure_reresolves_after_dependency_reingest(conn):
    repo = Repository(conn)
    _ingest(repo, "payment.py", [ParsedSymbol("process", "process", "function", 1, 2)], [])
    _ingest(repo, "checkout.py", [ParsedSymbol("order", "order", "function", 1, 2)],
            [ParsedEdge("order", "process", "calls")])
    repo.resolve_pending_edges()
    fid = repo.upsert_file_row("payment.py", "python", "h2")
    repo.replace_structure(fid, ParsedFile("payment.py", "python", "x",
                           [ParsedSymbol("process", "process", "function", 1, 5)], []))
    assert conn.execute("SELECT dst_symbol_id FROM edges WHERE dst_name='process'").fetchone()[0] is None
    repo.resolve_pending_edges()  # closure
    assert {c["src_qualname"] for c in repo.impact_of("process")} == {"order"}
```

- [ ] **Step 2: Run test, verify FAIL** — `AttributeError: ... 'resolve_pending_edges'`.

- [ ] **Step 3: Append resolver methods to `Repository`**

```python
    def resolve_pending_edges(self) -> None:
        by_name: dict[str, list[int]] = {}
        for name, sid in self._conn.execute("SELECT name, id FROM symbols").fetchall():
            by_name.setdefault(name, []).append(sid)
        pending = self._conn.execute(
            "SELECT id, dst_name FROM edges "
            "WHERE kind = 'calls' AND (resolution = 'pending' OR dst_symbol_id IS NULL)"
        ).fetchall()
        for edge_id, dst_name in pending:
            matches = by_name.get(dst_name, [])
            if len(matches) == 1:
                self._conn.execute(
                    "UPDATE edges SET dst_symbol_id = %s, resolution = 'resolved' WHERE id = %s",
                    (matches[0], edge_id),
                )
            else:
                resolution = "ambiguous" if len(matches) > 1 else "external"
                self._conn.execute(
                    "UPDATE edges SET dst_symbol_id = NULL, resolution = %s WHERE id = %s",
                    (resolution, edge_id),
                )

    def reresolve_all_edges(self) -> None:
        self._conn.execute(
            "UPDATE edges SET dst_symbol_id = NULL, resolution = 'pending' WHERE kind = 'calls'"
        )
        self.resolve_pending_edges()

    def impact_of(self, qualname: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT DISTINCT e.src_qualname, f.path FROM edges e "
            "JOIN files f ON f.id = e.file_id "
            "WHERE e.kind = 'calls' AND e.dst_symbol_id IN "
            "(SELECT id FROM symbols WHERE qualname = %s)",
            (qualname,),
        ).fetchall()
        return [{"src_qualname": r[0], "path": r[1]} for r in rows]
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): resolved edges with reverse-dependency closure"`

---

### Task 7: Repository — chunk sync (hash diff + batch embed)

**Files:**
- Modify: `memory-service/src/memory/repository.py` (append)
- Test: `memory-service/tests/test_chunk_sync.py`

**Interfaces:**
- Produces (return = number embedded): `sync_code_chunks(file_id, chunks, embedder) -> int`; `sync_doc_chunks(path, chunks, embedder) -> int`. Both embed only new/hash-changed chunks, upsert via `ON CONFLICT`, and delete absent keys. `embedder` is any `EmbeddingProvider`.

- [ ] **Step 1: Write `tests/test_chunk_sync.py`**

```python
from memory.chunking import CodeChunk, DocChunk
from memory.embeddings.local import LocalEmbeddingProvider
from memory.repository import Repository

EMB = LocalEmbeddingProvider()


def test_sync_code_embeds_then_skips_unchanged(conn):
    repo = Repository(conn)
    fid = repo.upsert_file_row("svc.py", "python", "h")
    chunks = [CodeChunk("helper#0", "helper", "hash1", "# svc.py > helper\nreturn 1")]
    assert repo.sync_code_chunks(fid, chunks, EMB) == 1
    assert repo.sync_code_chunks(fid, chunks, EMB) == 0


def test_sync_code_reembeds_changed_and_deletes_removed(conn):
    repo = Repository(conn)
    fid = repo.upsert_file_row("svc.py", "python", "h")
    repo.sync_code_chunks(fid, [
        CodeChunk("a#0", "a", "h1", "text a"),
        CodeChunk("b#0", "b", "h1", "text b"),
    ], EMB)
    embedded = repo.sync_code_chunks(fid, [
        CodeChunk("a#0", "a", "h2", "text a v2"),
        CodeChunk("c#0", "c", "h1", "text c"),
    ], EMB)
    assert embedded == 2
    keys = {r[0] for r in conn.execute("SELECT chunk_key FROM code_chunks").fetchall()}
    assert keys == {"a#0", "c#0"}


def test_sync_docs(conn):
    repo = Repository(conn)
    assert repo.sync_doc_chunks("r.md", [DocChunk("r.md#0", "r.md", "h1", "hello")], EMB) == 1
    assert repo.sync_doc_chunks("r.md", [DocChunk("r.md#0", "r.md", "h1", "hello")], EMB) == 0
```

- [ ] **Step 2: Run test, verify FAIL** — `AttributeError: ... 'sync_code_chunks'`.

- [ ] **Step 3: Append chunk-sync methods to `Repository`**

```python
    def sync_code_chunks(self, file_id: int, chunks, embedder) -> int:
        existing = {
            r[0]: r[1]
            for r in self._conn.execute(
                "SELECT chunk_key, content_hash FROM code_chunks WHERE file_id = %s", (file_id,)
            ).fetchall()
        }
        to_embed = [c for c in chunks if existing.get(c.chunk_key) != c.content_hash]
        vectors = embedder.embed([c.text for c in to_embed]) if to_embed else []
        vec_by_key = {c.chunk_key: v for c, v in zip(to_embed, vectors)}
        for c in chunks:
            if c.chunk_key in vec_by_key:
                self._conn.execute(
                    "INSERT INTO code_chunks (file_id, chunk_key, qualname, content_hash, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (file_id, chunk_key) DO UPDATE SET qualname = EXCLUDED.qualname, "
                    "content_hash = EXCLUDED.content_hash, text = EXCLUDED.text, embedding = EXCLUDED.embedding",
                    (file_id, c.chunk_key, c.qualname, c.content_hash, c.text, vec_by_key[c.chunk_key]),
                )
        keys = [c.chunk_key for c in chunks]
        if keys:
            self._conn.execute(
                "DELETE FROM code_chunks WHERE file_id = %s AND chunk_key <> ALL(%s)", (file_id, keys)
            )
        else:
            self._conn.execute("DELETE FROM code_chunks WHERE file_id = %s", (file_id,))
        return len(to_embed)

    def sync_doc_chunks(self, path: str, chunks, embedder) -> int:
        existing = {
            r[0]: r[1]
            for r in self._conn.execute(
                "SELECT chunk_key, content_hash FROM doc_chunks WHERE path = %s", (path,)
            ).fetchall()
        }
        to_embed = [c for c in chunks if existing.get(c.chunk_key) != c.content_hash]
        vectors = embedder.embed([c.text for c in to_embed]) if to_embed else []
        vec_by_key = {c.chunk_key: v for c, v in zip(to_embed, vectors)}
        for c in chunks:
            if c.chunk_key in vec_by_key:
                self._conn.execute(
                    "INSERT INTO doc_chunks (path, chunk_key, content_hash, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (path, chunk_key) DO UPDATE SET content_hash = EXCLUDED.content_hash, "
                    "text = EXCLUDED.text, embedding = EXCLUDED.embedding",
                    (path, c.chunk_key, c.content_hash, c.text, vec_by_key[c.chunk_key]),
                )
        keys = [c.chunk_key for c in chunks]
        if keys:
            self._conn.execute("DELETE FROM doc_chunks WHERE path = %s AND chunk_key <> ALL(%s)", (path, keys))
        else:
            self._conn.execute("DELETE FROM doc_chunks WHERE path = %s", (path,))
        return len(to_embed)
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): chunk sync with hash-diff and batched embedding"`

---

### Task 8: Repository — retrieval, linkage, queue, embedding-config guard

**Files:**
- Modify: `memory-service/src/memory/repository.py` (append)
- Test: `memory-service/tests/test_retrieval.py`

**Interfaces:**
- Produces (new methods):
  - `search_code(query_embedding, k=5) -> list[dict]` (`{"qualname","path","text"}`); `search_docs(query_embedding, k=5) -> list[dict]` (`{"path","text"}`).
  - `get_symbol(qualname) -> dict | None`; `add_spec_link(spec_path, symbol_qualname)`; `spec_for(qualname) -> list[str]`; `prune_spec_links() -> int`.
  - `enqueue(commit_sha, rel_path)`; `dequeue_pending(limit=100) -> list[tuple[int,str]]`; `mark_done(ids)`.
  - `get_embedding_config(collection) -> dict | None`; `ensure_embedding_config(collection, provider, model, dim) -> None` (insert if absent; raise `EmbeddingConfigMismatch` if present and different).

- [ ] **Step 1: Write `tests/test_retrieval.py`**

```python
import pytest

from memory.chunking import CodeChunk
from memory.embeddings.local import LocalEmbeddingProvider
from memory.models import ParsedFile, ParsedSymbol
from memory.repository import EmbeddingConfigMismatch, Repository

EMB = LocalEmbeddingProvider()


def _seed(conn):
    repo = Repository(conn)
    fid = repo.upsert_file_row("svc.py", "python", "h")
    repo.replace_structure(fid, ParsedFile("svc.py", "python", "x",
                           [ParsedSymbol("helper", "helper", "function", 1, 2)], []))
    repo.sync_code_chunks(fid, [CodeChunk("helper#0", "helper", "h1", "database connection helper")], EMB)
    return repo


def test_search_code(conn):
    repo = _seed(conn)
    results = repo.search_code(EMB.embed(["database helper"])[0], k=1)
    assert results[0]["qualname"] == "helper" and results[0]["path"] == "svc.py"


def test_get_symbol(conn):
    repo = _seed(conn)
    assert repo.get_symbol("helper")["kind"] == "function"
    assert repo.get_symbol("nope") is None


def test_spec_linkage_and_prune(conn):
    repo = _seed(conn)
    repo.add_spec_link("specs/x.md", "helper")
    assert repo.spec_for("helper") == ["specs/x.md"]
    repo.add_spec_link("specs/y.md", "ghost")
    assert repo.prune_spec_links() == 1
    assert repo.spec_for("ghost") == []


def test_queue_roundtrip(conn):
    repo = Repository(conn)
    repo.enqueue("sha1", "a.py")
    repo.enqueue("sha1", "b.py")
    pending = repo.dequeue_pending()
    assert {p[1] for p in pending} == {"a.py", "b.py"}
    repo.mark_done([p[0] for p in pending])
    assert repo.dequeue_pending() == []


def test_embedding_config_guard(conn):
    repo = Repository(conn)
    repo.ensure_embedding_config("code", "local", "bge", 384)
    repo.ensure_embedding_config("code", "local", "bge", 384)  # idempotent, no raise
    with pytest.raises(EmbeddingConfigMismatch):
        repo.ensure_embedding_config("code", "remote", "other", 768)
```

- [ ] **Step 2: Run test, verify FAIL** — `AttributeError: ... 'search_code'`.

- [ ] **Step 3: Append retrieval/linkage/queue/config methods to `Repository`**

```python
    def search_code(self, query_embedding, k: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT c.qualname, f.path, c.text FROM code_chunks c "
            "JOIN files f ON f.id = c.file_id ORDER BY c.embedding <=> %s LIMIT %s",
            (query_embedding, k),
        ).fetchall()
        return [{"qualname": r[0], "path": r[1], "text": r[2]} for r in rows]

    def search_docs(self, query_embedding, k: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT path, text FROM doc_chunks ORDER BY embedding <=> %s LIMIT %s",
            (query_embedding, k),
        ).fetchall()
        return [{"path": r[0], "text": r[1]} for r in rows]

    def get_symbol(self, qualname: str) -> dict | None:
        row = self._conn.execute(
            "SELECT s.qualname, s.name, s.kind, f.path, s.start_line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.qualname = %s LIMIT 1",
            (qualname,),
        ).fetchone()
        if not row:
            return None
        return {"qualname": row[0], "name": row[1], "kind": row[2],
                "path": row[3], "start_line": row[4], "end_line": row[5]}

    def add_spec_link(self, spec_path: str, symbol_qualname: str) -> None:
        self._conn.execute(
            "INSERT INTO spec_links (spec_path, symbol_qualname) VALUES (%s, %s)",
            (spec_path, symbol_qualname),
        )

    def spec_for(self, qualname: str) -> list[str]:
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT spec_path FROM spec_links WHERE symbol_qualname = %s ORDER BY spec_path",
                (qualname,),
            ).fetchall()
        ]

    def prune_spec_links(self) -> int:
        return self._conn.execute(
            "DELETE FROM spec_links WHERE symbol_qualname NOT IN (SELECT qualname FROM symbols)"
        ).rowcount

    def enqueue(self, commit_sha: str, rel_path: str) -> None:
        self._conn.execute(
            "INSERT INTO ingest_queue (commit_sha, rel_path) VALUES (%s, %s)", (commit_sha, rel_path)
        )

    def dequeue_pending(self, limit: int = 100) -> list[tuple[int, str]]:
        return [
            (r[0], r[1])
            for r in self._conn.execute(
                "SELECT id, rel_path FROM ingest_queue WHERE status = 'pending' ORDER BY id LIMIT %s",
                (limit,),
            ).fetchall()
        ]

    def mark_done(self, ids: list[int]) -> None:
        if ids:
            self._conn.execute("UPDATE ingest_queue SET status = 'done' WHERE id = ANY(%s)", (ids,))

    def get_embedding_config(self, collection: str) -> dict | None:
        row = self._conn.execute(
            "SELECT provider, model, dim FROM embedding_config WHERE collection = %s", (collection,)
        ).fetchone()
        return None if not row else {"provider": row[0], "model": row[1], "dim": row[2]}

    def ensure_embedding_config(self, collection: str, provider: str, model: str, dim: int) -> None:
        existing = self.get_embedding_config(collection)
        wanted = {"provider": provider, "model": model, "dim": dim}
        if existing is None:
            self._conn.execute(
                "INSERT INTO embedding_config (collection, provider, model, dim) VALUES (%s, %s, %s, %s)",
                (collection, provider, model, dim),
            )
        elif existing != wanted:
            raise EmbeddingConfigMismatch(
                f"{collection}: stored {existing} != configured {wanted}; reconcile/re-embed required"
            )
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): retrieval, linkage prune, queue, embedding-config guard"`

---

### Task 9: Worker — process a file + drain the queue

**Files:**
- Create: `memory-service/src/memory/worker.py`
- Test: `memory-service/tests/test_worker.py`

**Interfaces:**
- Produces:
  - `memory.worker.content_hash(text) -> str` (sha256 of raw file text).
  - `memory.worker.Worker(repo, code_embedder, doc_embedder, parser)` — separate embedders for code vs docs.
  - `Worker.process_file(root, rel_path) -> str` — `"ingested"`/`"skipped"`/`"deleted"`. `.py`: hash-skip; else replace structure, `resolve_pending_edges()` (closure), `sync_code_chunks(..., code_embedder)`. `.md`: hash-skip; else `sync_doc_chunks(..., doc_embedder)`. Missing → `delete_file` + `sync_doc_chunks(path, [], doc_embedder)` → `"deleted"`. Other ext → `"skipped"`.
  - `Worker.drain(repo_root) -> dict` — dequeue, dedupe, process, mark done; returns `{"ingested":[...], "skipped":[...], "deleted":[...]}`.

- [ ] **Step 1: Write `tests/test_worker.py`**

```python
from pathlib import Path

from memory.embeddings.local import LocalEmbeddingProvider
from memory.parser.python_parser import PythonParser
from memory.repository import Repository
from memory.worker import Worker, content_hash

EMB = LocalEmbeddingProvider()


def _worker(conn):
    return Worker(Repository(conn), EMB, EMB, PythonParser())


def test_content_hash_stable():
    assert content_hash("abc") == content_hash("abc") != content_hash("abd")


def test_process_python_then_skip(conn, tmp_path: Path):
    (tmp_path / "svc.py").write_text("def helper():\n    return 1\n")
    w = _worker(conn)
    assert w.process_file(str(tmp_path), "svc.py") == "ingested"
    assert conn.execute("SELECT count(*) FROM code_chunks").fetchone()[0] == 1
    assert w.process_file(str(tmp_path), "svc.py") == "skipped"


def test_process_deletes_missing(conn, tmp_path: Path):
    (tmp_path / "svc.py").write_text("def helper():\n    return 1\n")
    w = _worker(conn)
    w.process_file(str(tmp_path), "svc.py")
    (tmp_path / "svc.py").unlink()
    assert w.process_file(str(tmp_path), "svc.py") == "deleted"
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 0


def test_drain_processes_queue_and_closure(conn, tmp_path: Path):
    (tmp_path / "payment.py").write_text("def process():\n    return 1\n")
    (tmp_path / "checkout.py").write_text("def order():\n    return process()\n")
    repo = Repository(conn)
    repo.enqueue("sha1", "payment.py")
    repo.enqueue("sha1", "checkout.py")
    result = Worker(repo, EMB, EMB, PythonParser()).drain(str(tmp_path))
    assert set(result["ingested"]) == {"payment.py", "checkout.py"}
    assert {c["src_qualname"] for c in repo.impact_of("process")} == {"order"}
    assert repo.dequeue_pending() == []
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.worker'`.

- [ ] **Step 3: Create `src/memory/worker.py`**

```python
import hashlib
from pathlib import Path

from memory.chunking import chunk_code, chunk_docs
from memory.embeddings.base import EmbeddingProvider
from memory.parser.base import LanguageParser
from memory.repository import Repository


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()


class Worker:
    def __init__(
        self,
        repo: Repository,
        code_embedder: EmbeddingProvider,
        doc_embedder: EmbeddingProvider,
        parser: LanguageParser,
    ) -> None:
        self._repo = repo
        self._code_embedder = code_embedder
        self._doc_embedder = doc_embedder
        self._parser = parser

    def process_file(self, root: str, rel_path: str) -> str:
        abs_path = Path(root) / rel_path
        if not abs_path.exists():
            self._repo.delete_file(rel_path)
            self._repo.sync_doc_chunks(rel_path, [], self._doc_embedder)
            return "deleted"

        text = abs_path.read_text()
        digest = content_hash(text)
        if self._repo.file_hash(rel_path) == digest:
            return "skipped"

        if rel_path.endswith(".py"):
            parsed = self._parser.parse(rel_path, text)
            file_id = self._repo.upsert_file_row(rel_path, "python", digest)
            self._repo.replace_structure(file_id, parsed)
            self._repo.resolve_pending_edges()  # closure
            self._repo.sync_code_chunks(file_id, chunk_code(parsed), self._code_embedder)
            return "ingested"

        if rel_path.endswith(".md"):
            self._repo.upsert_file_row(rel_path, "markdown", digest)
            self._repo.sync_doc_chunks(rel_path, chunk_docs(rel_path, text), self._doc_embedder)
            return "ingested"

        return "skipped"

    def drain(self, repo_root: str) -> dict:
        pending = self._repo.dequeue_pending()
        result: dict = {"ingested": [], "skipped": [], "deleted": []}
        seen: set[str] = set()
        ids: list[int] = []
        for queue_id, rel_path in pending:
            ids.append(queue_id)
            if rel_path in seen:
                continue
            seen.add(rel_path)
            result.setdefault(self.process_file(repo_root, rel_path), []).append(rel_path)
        self._repo.mark_done(ids)
        return result
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): worker with per-collection embedders + queue drain + closure"`

---

### Task 10: Reconcile job (full re-scan + drift repair)

**Files:**
- Create: `memory-service/src/memory/reconcile.py`
- Test: `memory-service/tests/test_reconcile.py`

**Interfaces:**
- Produces: `memory.reconcile.scan_paths(root) -> list[str]` (`.py`/`.md`, relative, sorted); `memory.reconcile.reconcile(repo, worker, root) -> dict` — process all on-disk files, delete orphan DB files, `reresolve_all_edges()`, `prune_spec_links()`; returns `{"processed", "removed", "pruned_links"}`.

- [ ] **Step 1: Write `tests/test_reconcile.py`**

```python
from pathlib import Path

from memory.embeddings.local import LocalEmbeddingProvider
from memory.parser.python_parser import PythonParser
from memory.reconcile import reconcile, scan_paths
from memory.repository import Repository
from memory.worker import Worker

EMB = LocalEmbeddingProvider()


def test_scan_paths(tmp_path: Path):
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.md").write_text("# t\n")
    (tmp_path / "c.txt").write_text("ignore\n")
    assert scan_paths(str(tmp_path)) == ["a.py", "b.md"]


def test_reconcile_removes_orphans_and_prunes(conn, tmp_path: Path):
    (tmp_path / "svc.py").write_text("def helper():\n    return 1\n")
    repo = Repository(conn)
    worker = Worker(repo, EMB, EMB, PythonParser())
    worker.process_file(str(tmp_path), "svc.py")
    repo.add_spec_link("specs/x.md", "helper")
    repo.upsert_file_row("gone.py", "python", "h")   # orphan
    repo.add_spec_link("specs/y.md", "ghost")        # dangling
    result = reconcile(repo, worker, str(tmp_path))
    assert "gone.py" in result["removed"]
    assert result["pruned_links"] == 1
    assert repo.list_db_files() == ["svc.py"]
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.reconcile'`.

- [ ] **Step 3: Create `src/memory/reconcile.py`**

```python
from pathlib import Path

from memory.repository import Repository
from memory.worker import Worker

_SUPPORTED = (".py", ".md")


def scan_paths(root: str) -> list[str]:
    root_path = Path(root)
    return sorted(
        str(p.relative_to(root_path))
        for p in root_path.rglob("*")
        if p.is_file() and p.suffix in _SUPPORTED
    )


def reconcile(repo: Repository, worker: Worker, root: str) -> dict:
    on_disk = scan_paths(root)
    for rel_path in on_disk:
        worker.process_file(root, rel_path)
    on_disk_set = set(on_disk)
    removed = [p for p in repo.list_db_files() if p not in on_disk_set]
    for rel_path in removed:
        repo.delete_file(rel_path)
    repo.reresolve_all_edges()
    pruned = repo.prune_spec_links()
    return {"processed": len(on_disk), "removed": removed, "pruned_links": pruned}
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): reconcile job for full re-scan and drift repair"`

---

### Task 11: MCP server

**Files:**
- Create: `memory-service/src/memory/mcp_server.py`
- Test: `memory-service/tests/test_mcp_server.py`

**Interfaces:**
- Produces: `memory.mcp_server.build_server(repo, code_embedder, doc_embedder) -> mcp.server.Server` (tools `search_code`, `search_docs`, `get_symbol`, `impact_of`, `spec_for`, `add_knowledge`; `search_code` uses `code_embedder`, `search_docs` uses `doc_embedder`); `memory.mcp_server.main()` wiring config/factory/guard and serving over stdio.

- [ ] **Step 1: Write `tests/test_mcp_server.py`**

```python
import json

import pytest

from memory.chunking import CodeChunk
from memory.embeddings.local import LocalEmbeddingProvider
from memory.mcp_server import build_server
from memory.models import ParsedFile, ParsedSymbol
from memory.repository import Repository

EMB = LocalEmbeddingProvider()


@pytest.fixture()
def server(conn):
    repo = Repository(conn)
    fid = repo.upsert_file_row("svc.py", "python", "h")
    repo.replace_structure(fid, ParsedFile("svc.py", "python", "x",
                           [ParsedSymbol("helper", "helper", "function", 1, 2)], []))
    repo.sync_code_chunks(fid, [CodeChunk("helper#0", "helper", "h1", "database connection helper")], EMB)
    return build_server(repo, EMB, EMB)


def _text(result):
    item = result[0] if isinstance(result, (list, tuple)) else result
    return item.text


@pytest.mark.anyio
async def test_search_code_tool(server):
    result = await server.call_tool("search_code", {"query": "database helper", "k": 1})
    assert json.loads(_text(result))[0]["qualname"] == "helper"


@pytest.mark.anyio
async def test_add_knowledge_then_spec_for(server):
    await server.call_tool("add_knowledge", {"spec_path": "specs/x.md", "symbol_qualname": "helper"})
    result = await server.call_tool("spec_for", {"qualname": "helper"})
    assert json.loads(_text(result)) == ["specs/x.md"]
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.mcp_server'`.

- [ ] **Step 3: Create `src/memory/mcp_server.py`**

```python
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from memory.config import Settings
from memory.db import apply_schema, connect
from memory.embeddings.base import EmbeddingProvider
from memory.embeddings.factory import build_embedder
from memory.repository import Repository


def build_server(repo: Repository, code_embedder: EmbeddingProvider, doc_embedder: EmbeddingProvider) -> Server:
    server = Server("memory-service")

    def _obj(props: dict, required: list[str]) -> dict:
        return {"type": "object", "properties": props, "required": required}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        q = {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}}
        ql = {"qualname": {"type": "string"}}
        return [
            Tool(name="search_code", description="Semantic search over code chunks.", inputSchema=_obj(q, ["query"])),
            Tool(name="search_docs", description="Semantic search over doc/spec chunks.", inputSchema=_obj(q, ["query"])),
            Tool(name="get_symbol", description="Look up a symbol by qualified name.", inputSchema=_obj(ql, ["qualname"])),
            Tool(name="impact_of", description="List callers of a symbol (resolved edges).", inputSchema=_obj(ql, ["qualname"])),
            Tool(name="spec_for", description="List spec files linked to a symbol.", inputSchema=_obj(ql, ["qualname"])),
            Tool(name="add_knowledge", description="Link a spec file to a symbol.",
                 inputSchema=_obj({"spec_path": {"type": "string"}, "symbol_qualname": {"type": "string"}},
                                  ["spec_path", "symbol_qualname"])),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "search_code":
            payload = repo.search_code(code_embedder.embed([arguments["query"]])[0], arguments.get("k", 5))
        elif name == "search_docs":
            payload = repo.search_docs(doc_embedder.embed([arguments["query"]])[0], arguments.get("k", 5))
        elif name == "get_symbol":
            payload = repo.get_symbol(arguments["qualname"])
        elif name == "impact_of":
            payload = repo.impact_of(arguments["qualname"])
        elif name == "spec_for":
            payload = repo.spec_for(arguments["qualname"])
        elif name == "add_knowledge":
            repo.add_spec_link(arguments["spec_path"], arguments["symbol_qualname"])
            payload = {"status": "linked"}
        else:
            payload = {"error": f"unknown tool {name}"}
        return [TextContent(type="text", text=json.dumps(payload))]

    return server


def main() -> None:
    import asyncio

    settings = Settings.from_env()
    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    repo.ensure_embedding_config("code", settings.code_embed.provider, settings.code_embed.model, settings.code_embed.dim)
    repo.ensure_embedding_config("doc", settings.doc_embed.provider, settings.doc_embed.model, settings.doc_embed.dim)
    server = build_server(repo, build_embedder(settings.code_embed), build_embedder(settings.doc_embed))

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test, verify PASS.** (If the installed `mcp` `call_tool` returns a bare value, `_text` already unwraps both shapes.)

- [ ] **Step 5: Commit** — `git commit -m "feat(memory): MCP server with per-collection embedders + config guard"`

---

### Task 12: Containerization, TEI embeddings service, enqueue hook, worker

**Files:**
- Create: `memory-service/Dockerfile`, `memory-service/hooks/post-commit`, `memory-service/src/memory/enqueue.py`, `memory-service/src/memory/run_worker.py`, `memory-service/README.md`
- Modify: `memory-service/docker-compose.yml` (add `embeddings`, `memory`, `worker`)
- Test: `memory-service/tests/test_enqueue.py`

**Interfaces:**
- Produces:
  - `memory.enqueue.enqueue_changed(root, commit_sha, rel_paths) -> int`; `memory.enqueue.changed_files(root) -> list[str]`; `memory.enqueue.head_sha(root) -> str`.
  - `memory.run_worker.drain_once(root) -> dict` (factory-built per-collection embedders + config guard, then drain); `memory.run_worker.serve(root, interval=2.0)`.
  - `hooks/post-commit` — enqueues `HEAD`'s changed files (fast; no embedding).

- [ ] **Step 1: Write `tests/test_enqueue.py`**

```python
from memory.enqueue import enqueue_changed
from memory.repository import Repository


def test_enqueue_changed_supported_only(conn, tmp_path):
    assert enqueue_changed(str(tmp_path), "sha1", ["a.py", "b.md", "c.txt"]) == 2
    pending = Repository(conn).dequeue_pending()
    assert {p[1] for p in pending} == {"a.py", "b.md"}
```

- [ ] **Step 2: Run test, verify FAIL** — `ModuleNotFoundError: No module named 'memory.enqueue'`.

- [ ] **Step 3: Create `src/memory/enqueue.py`**

```python
import subprocess
import sys

from memory.config import Settings
from memory.db import apply_schema, connect
from memory.repository import Repository

_SUPPORTED = (".py", ".md")


def enqueue_changed(root: str, commit_sha: str, rel_paths: list[str]) -> int:
    settings = Settings.from_env()
    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    count = 0
    for rel_path in rel_paths:
        if rel_path.endswith(_SUPPORTED):
            repo.enqueue(commit_sha, rel_path)
            count += 1
    return count


def changed_files(root: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line.endswith(_SUPPORTED)]


def head_sha(root: str) -> str:
    return subprocess.run(
        ["git", "-C", root, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(f"enqueued {enqueue_changed(root, head_sha(root), changed_files(root))} files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test, verify PASS.**

- [ ] **Step 5: Create `src/memory/run_worker.py`**

```python
import sys
import time

from memory.config import Settings
from memory.db import apply_schema, connect
from memory.embeddings.factory import build_embedder
from memory.parser.python_parser import PythonParser
from memory.repository import Repository
from memory.worker import Worker


def _worker() -> tuple[Worker, str]:
    settings = Settings.from_env()
    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    repo.ensure_embedding_config("code", settings.code_embed.provider, settings.code_embed.model, settings.code_embed.dim)
    repo.ensure_embedding_config("doc", settings.doc_embed.provider, settings.doc_embed.model, settings.doc_embed.dim)
    worker = Worker(repo, build_embedder(settings.code_embed), build_embedder(settings.doc_embed), PythonParser())
    return worker, settings.database_url


def drain_once(root: str) -> dict:
    worker, _ = _worker()
    return worker.drain(root)


def serve(root: str, interval: float = 2.0) -> None:
    worker, _ = _worker()
    while True:
        worker.drain(root)
        time.sleep(interval)


if __name__ == "__main__":
    serve(sys.argv[1] if len(sys.argv) > 1 else "/project")
```

- [ ] **Step 6: Create `hooks/post-commit`**

```bash
#!/usr/bin/env bash
# Fast: enqueue files changed in the last commit. No embedding here.
# Install: cp memory-service/hooks/post-commit <project>/.git/hooks/ && chmod +x.
# Requires DATABASE_URL to point at the memory Postgres.
set -euo pipefail
PROJECT_ROOT="$(git rev-parse --show-toplevel)"
uv run --project "${MEMORY_SERVICE_DIR:-memory-service}" python -m memory.enqueue "$PROJECT_ROOT"
```

- [ ] **Step 7: Create `Dockerfile`** (model-free: no embedding weights baked in)

```dockerfile
FROM python:3.12-slim

RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY sql ./sql
RUN uv sync

ENV DATABASE_URL=postgresql://postgres:postgres@db:5432/memory
CMD ["uv", "run", "python", "-m", "memory.mcp_server"]
```

- [ ] **Step 8: Add `embeddings`, `memory`, `worker` to `docker-compose.yml`**

Append under `services:` (sibling of `db`). NOTE: confirm the TEI tag for your arch; the `worker`/`memory` point at it by default, making the running stack use the remote provider.

```yaml
  embeddings:
    image: ghcr.io/huggingface/text-embeddings-inference:cpu-1.5
    command: ["--model-id", "BAAI/bge-small-en-v1.5"]
    ports:
      - "8080:80"

  memory:
    build: .
    depends_on:
      db:
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
    stdin_open: true
    tty: true

  worker:
    build: .
    depends_on:
      db:
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
      - ${PROJECT_DIR:-./}:/project:ro
    command: ["uv", "run", "python", "-m", "memory.run_worker", "/project"]
```

- [ ] **Step 9: Create `README.md`**

```markdown
# Memory Service (Phase 1)

Persistent knowledge layer: structure graph (resolved edges) + semantic embeddings
+ spec linkage, kept fresh by enqueue → worker → reconcile, exposed over MCP.
The embedding model runs as a **separate service** (default TEI); our images are model-free.

## Run

```bash
docker compose up -d db          # Postgres + pgvector
uv sync --extra dev
uv run pytest -v                 # full suite (db running; uses local fastembed, no TEI needed)
docker compose up -d embeddings memory worker   # running stack (remote TEI provider)
```

## Choosing embedding models

Per collection, via env (independently for code and docs):
`CODE_EMBED_PROVIDER` (`local`|`remote`), `CODE_EMBED_URL`, `CODE_EMBED_MODEL`, `CODE_EMBED_DIM`
and the `DOC_EMBED_*` equivalents. Changing a model's dimension requires a reconcile/re-embed
(the `embedding_config` guard refuses silent mixing).

## Reconcile (drift safety net)

`uv run python -m memory.reconcile /path/to/project`

## MCP tools

`search_code`, `search_docs`, `get_symbol`, `impact_of`, `spec_for`, `add_knowledge`.
```

- [ ] **Step 10: Verify full suite + image build**

```bash
uv run pytest -v
docker compose build memory worker
```
Expected: all tests PASS; images build.

- [ ] **Step 11: Commit** — `git commit -m "feat(memory): containerize, TEI embeddings service, enqueue hook, worker"`

---

## Self-Review notes (for the implementer)

- **Spec coverage:** Implements spec §5.2 in full — resolved-edge structure graph (Tasks 5–6), semantic layer (Tasks 4, 7), linkage + prune (Tasks 8, 10), enqueue→worker→reconcile (Tasks 8–12, 10), AST/breadcrumb chunking (Task 4), chunk-hash diff (Task 7), reverse-dependency closure (Task 6 + worker), and **external per-collection embeddings** (Task 2 providers + factory; Task 1 templated dims + `embedding_config`; Task 8 guard; Tasks 9/11/12 wiring; Task 12 TEI service). Phases 2–3 remain out of scope.
- **Embedding decoupling:** images are model-free; `local` fastembed is the test/offline default (tests never need TEI), `remote` TEI is the running-stack default. Per-collection dimension is fixed in pgvector and recorded in `embedding_config`; mismatch raises `EmbeddingConfigMismatch` → reconcile/re-embed. Code and docs are independently configurable.
- **Closure correctness:** the re-resolve set is exactly `pending OR dst_symbol_id IS NULL` = the changed file's new edges plus the edges into it that `ON DELETE SET NULL` invalidated; `reresolve_all_edges()` (reconcile) is the global safety net.
- **Intentional v1 simplifications (→ spec escalation paths):** Python + Markdown only; name-based symbol resolution (unique → resolved, collisions → ambiguous); HNSW indexes created but recall not tuned; worker batches per file.
- **Type consistency:** `EmbeddingProvider.embed`/`dim` honored by both providers and the factory; `Worker(repo, code_embedder, doc_embedder, parser)` matches its construction in Tasks 9/10 and `build_server(repo, code_embedder, doc_embedder)` in Task 11; `chunk_key`/`content_hash`/`qualname` consistent across Task 4/7/8; `EmbeddingConfigMismatch` defined in `repository.py` (Task 5) and used in Tasks 8/9/11/12.
```
