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
