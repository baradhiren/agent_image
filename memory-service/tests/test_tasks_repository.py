from memory.tasks import TaskRepository


def test_create_returns_id_and_defaults(conn):
    repo = TaskRepository(conn)
    tid = repo.create(spec_ref="tasks/x.md", title="Add CSV export", assignee_role="developer")
    row = repo.get(tid)
    assert row["id"] == tid
    assert row["spec_ref"] == "tasks/x.md"
    assert row["title"] == "Add CSV export"
    assert row["assignee_role"] == "developer"
    assert row["status"] == "in_progress"
    assert row["round"] == 0
    assert row["review_status"] == "pending"
    assert row["branch"] is None
    assert row["artifacts"] == []


def test_set_branch_round_status(conn):
    repo = TaskRepository(conn)
    tid = repo.create("s.md", "t", "developer")
    repo.set_branch(tid, "feat/1-t")
    repo.set_round(tid, 2)
    repo.set_status(tid, "approved")
    row = repo.get(tid)
    assert row["branch"] == "feat/1-t"
    assert row["round"] == 2
    assert row["status"] == "approved"


def test_record_developer_result_and_review(conn):
    repo = TaskRepository(conn)
    tid = repo.create("s.md", "t", "developer")
    repo.record_developer_result(tid, summary="did the thing", artifacts=["abc123", "def456"])
    repo.record_review(tid, review_status="needs_changes", review_notes="fix the edge case")
    row = repo.get(tid)
    assert row["summary"] == "did the thing"
    assert row["artifacts"] == ["abc123", "def456"]
    assert row["review_status"] == "needs_changes"
    assert row["review_notes"] == "fix the edge case"


def test_get_unknown_returns_none(conn):
    assert TaskRepository(conn).get(999999) is None
