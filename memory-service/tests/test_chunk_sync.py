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
