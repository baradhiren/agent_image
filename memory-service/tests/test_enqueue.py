from memory.enqueue import enqueue_changed
from memory.repository import Repository


def test_enqueue_changed_supported_only(conn, tmp_path):
    assert enqueue_changed(str(tmp_path), "sha1", ["a.py", "b.md", "c.txt"]) == 2
    pending = Repository(conn).dequeue_pending()
    assert {p[1] for p in pending} == {"a.py", "b.md"}


def test_enqueue_changed_includes_ts_js(conn, tmp_path):
    assert enqueue_changed(str(tmp_path), "sha2", ["app.ts", "comp.tsx", "x.go"]) == 2
    pending = Repository(conn).dequeue_pending()
    assert {p[1] for p in pending} == {"app.ts", "comp.tsx"}
