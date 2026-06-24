from memory.models import ParsedEdge, ParsedFile, ParsedSymbol
from memory.repository import Repository


def _ingest(repo, path, language, symbols, edges):
    fid = repo.upsert_file_row(path, language, "h")
    repo.replace_structure(fid, ParsedFile(path, language, "x", symbols, edges))


def test_no_cross_language_resolution(conn):
    repo = Repository(conn)
    # Python 'process' defined; Python caller resolves to it.
    _ingest(repo, "p.py", "python",
            [ParsedSymbol("process", "process", "function", 1, 2)], [])
    _ingest(repo, "caller.py", "python",
            [ParsedSymbol("run", "run", "function", 1, 2)],
            [ParsedEdge("run", "process", "calls")])
    # A TypeScript 'process' with the same name must NOT capture the Python call.
    _ingest(repo, "svc.ts", "javascript",
            [ParsedSymbol("process", "process", "function", 1, 2)], [])
    repo.resolve_pending_edges()
    callers = {c["src_qualname"] for c in repo.impact_of("process")}
    assert callers == {"run"}
    row = conn.execute(
        "SELECT f.language FROM edges e "
        "JOIN symbols s ON s.id = e.dst_symbol_id "
        "JOIN files f ON f.id = s.file_id WHERE e.dst_name = 'process'"
    ).fetchone()
    assert row[0] == "python"


def test_ts_resolves_to_js_same_family(conn):
    repo = Repository(conn)
    _ingest(repo, "util.js", "javascript",
            [ParsedSymbol("helper", "helper", "function", 1, 2)], [])
    _ingest(repo, "svc.ts", "javascript",
            [ParsedSymbol("order", "order", "function", 1, 2)],
            [ParsedEdge("order", "helper", "calls")])
    repo.resolve_pending_edges()
    assert {c["src_qualname"] for c in repo.impact_of("helper")} == {"order"}
