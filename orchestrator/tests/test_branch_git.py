import subprocess

from orchestrator.branch import (
    checkout, create_task_branch, current_branch, diff_summary,
)


def _init_repo(path):
    def g(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True,
                       capture_output=True, text=True)
    g("init", "-b", "main")
    g("config", "user.email", "t@t.t")
    g("config", "user.name", "t")
    (path / "a.txt").write_text("one\n")
    g("add", "a.txt")
    g("commit", "-m", "init")
    return g


def test_current_branch(tmp_path):
    _init_repo(tmp_path)
    assert current_branch(str(tmp_path)) == "main"


def test_create_task_branch_switches(tmp_path):
    _init_repo(tmp_path)
    create_task_branch(str(tmp_path), "feat/1-x", base="main")
    assert current_branch(str(tmp_path)) == "feat/1-x"


def test_diff_summary_reports_changes(tmp_path):
    g = _init_repo(tmp_path)
    create_task_branch(str(tmp_path), "feat/1-x", base="main")
    (tmp_path / "b.txt").write_text("two\n")
    g("add", "b.txt")
    g("commit", "-m", "add b")
    out = diff_summary(str(tmp_path), base="main", branch="feat/1-x")
    assert "b.txt" in out


def test_checkout_restores_base(tmp_path):
    _init_repo(tmp_path)
    create_task_branch(str(tmp_path), "feat/1-x", base="main")
    assert current_branch(str(tmp_path)) == "feat/1-x"
    checkout(str(tmp_path), "main")
    assert current_branch(str(tmp_path)) == "main"
