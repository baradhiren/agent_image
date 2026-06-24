from pathlib import Path

from memory.embeddings.local import LocalEmbeddingProvider
from memory.repository import Repository
from memory.worker import Worker, content_hash

EMB = LocalEmbeddingProvider()


def _worker(conn):
    return Worker(Repository(conn), EMB, EMB)


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
    result = Worker(repo, EMB, EMB).drain(str(tmp_path))
    assert set(result["ingested"]) == {"payment.py", "checkout.py"}
    assert {c["src_qualname"] for c in repo.impact_of("process")} == {"order"}
    assert repo.dequeue_pending() == []
