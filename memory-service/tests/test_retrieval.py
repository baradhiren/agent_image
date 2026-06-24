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
