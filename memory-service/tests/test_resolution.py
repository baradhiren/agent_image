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
