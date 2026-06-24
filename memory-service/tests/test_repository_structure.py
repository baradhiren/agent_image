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
