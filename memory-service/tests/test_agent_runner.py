# memory-service/tests/test_agent_runner.py
from memory import agent_runner
from memory.tasks import TaskRepository


def test_parse_verdict_approved():
    assert agent_runner.parse_verdict("looks good\nVERDICT: approved")[0] == "approved"


def test_parse_verdict_needs_changes_with_notes():
    status, notes = agent_runner.parse_verdict("issues:\n- x\nVERDICT: needs_changes")
    assert status == "needs_changes"
    assert "issues" in notes


def test_parse_verdict_defaults_to_needs_changes():
    assert agent_runner.parse_verdict("no verdict line here")[0] == "needs_changes"


def test_build_prompt_developer_includes_notes():
    p = agent_runner.build_prompt("developer", "do X", "fix the bug", "feat/1-x")
    assert "do X" in p and "fix the bug" in p


def test_run_developer_records_result(conn, tmp_path):
    repo = TaskRepository(conn)
    (tmp_path / "t.md").write_text("# T\n\ndo it")
    tid = repo.create("t.md", "T", "developer")  # spec_ref relative to workspace
    repo.set_branch(tid, "feat/1-t")
    agent_runner.run("developer", tid, 1, repo=repo, workspace=str(tmp_path),
                     run_session=lambda prompt: "I implemented it.",
                     head_sha=lambda ws: "abc123")
    row = repo.get(tid)
    assert row["summary"] == "I implemented it."
    assert row["artifacts"] == ["abc123"]


def test_run_reviewer_records_verdict(conn, tmp_path):
    repo = TaskRepository(conn)
    (tmp_path / "t.md").write_text("# T\n\ndo it")
    tid = repo.create("t.md", "T", "developer")
    repo.set_branch(tid, "feat/1-t")
    agent_runner.run("reviewer", tid, 1, repo=repo, workspace=str(tmp_path),
                     run_session=lambda prompt: "all good\nVERDICT: approved",
                     head_sha=lambda ws: "abc123")
    row = repo.get(tid)
    assert row["review_status"] == "approved"
