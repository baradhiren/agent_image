from pathlib import Path

from memory.embeddings.local import LocalEmbeddingProvider
from memory.repository import Repository
from memory.worker import Worker

EMB = LocalEmbeddingProvider()


def test_ingest_typescript_file(conn, tmp_path: Path):
    (tmp_path / "svc.ts").write_text(
        "export function helper(): number { return 1; }\n"
    )
    Worker(Repository(conn), EMB, EMB).process_file(str(tmp_path), "svc.ts")
    lang = conn.execute("SELECT language FROM files WHERE path='svc.ts'").fetchone()[0]
    assert lang == "javascript"
    assert conn.execute("SELECT count(*) FROM symbols WHERE qualname='helper'").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM code_chunks").fetchone()[0] >= 1


def test_unsupported_extension_skipped(conn, tmp_path: Path):
    (tmp_path / "data.json").write_text("{}\n")
    assert Worker(Repository(conn), EMB, EMB).process_file(str(tmp_path), "data.json") == "skipped"
