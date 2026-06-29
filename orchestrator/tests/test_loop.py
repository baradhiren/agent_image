# orchestrator/tests/test_loop.py
from orchestrator.loop import run_task
from orchestrator.seams import RunnerResult


class FakeRepo:
    """In-memory stand-in for TaskRepository."""
    def __init__(self, review_sequence):
        self._review = list(review_sequence)  # e.g. ["needs_changes", "approved"]
        self.rows = {}
        self._next = 1
        self.calls = []

    def create(self, spec_ref, title, assignee_role):
        tid = self._next; self._next += 1
        self.rows[tid] = {"id": tid, "spec_ref": spec_ref, "title": title,
                          "assignee_role": assignee_role, "branch": None,
                          "status": "in_progress", "round": 0,
                          "review_status": "pending", "review_notes": None,
                          "summary": None, "artifacts": []}
        return tid

    def set_branch(self, tid, branch): self.rows[tid]["branch"] = branch
    def set_round(self, tid, r): self.rows[tid]["round"] = r
    def set_status(self, tid, s): self.rows[tid]["status"] = s; self.calls.append(("status", s))
    def record_review(self, tid, rs, rn):
        self.rows[tid]["review_status"] = rs; self.rows[tid]["review_notes"] = rn
    def record_developer_result(self, tid, summary, artifacts):
        self.rows[tid]["summary"] = summary; self.rows[tid]["artifacts"] = artifacts
    def get(self, tid): return self.rows.get(tid)


def _runner_ok(repo, role_writes):
    # returns a run_* callable that records a review verdict when role_writes set
    def run(task_id, round):
        if role_writes is not None:
            rs = role_writes.pop(0)
            repo.record_review(task_id, rs, f"notes-{rs}")
        return RunnerResult(ok=True, exit_code=0)
    return run


def _write_task(tmp_path):
    f = tmp_path / "t.md"; f.write_text("# Add export\n\nbody"); return str(f)


def test_approve_round_1(tmp_path):
    repo = FakeRepo([])
    verdicts = ["approved"]
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None,
                      run_developer=lambda t, r: RunnerResult(True, 0),
                      run_reviewer=_runner_ok(repo, verdicts), cap=2)
    assert report.status == "approved"
    assert report.review_status == "approved"
    assert report.rounds == 1
    assert report.branch == "feat/1-add-export"


def test_needs_changes_then_approve(tmp_path):
    repo = FakeRepo([])
    verdicts = ["needs_changes", "approved"]
    seen_rounds = []
    def dev(t, r): seen_rounds.append(r); return RunnerResult(True, 0)
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None, run_developer=dev,
                      run_reviewer=_runner_ok(repo, verdicts), cap=2)
    assert report.status == "approved"
    assert report.rounds == 2
    assert seen_rounds == [1, 2]  # developer re-run on round 2


def test_cap_reached_is_needs_changes_not_failed(tmp_path):
    repo = FakeRepo([])
    verdicts = ["needs_changes", "needs_changes"]
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None,
                      run_developer=lambda t, r: RunnerResult(True, 0),
                      run_reviewer=_runner_ok(repo, verdicts), cap=2)
    assert report.status == "needs_changes"
    assert report.rounds == 2


def test_developer_failure_marks_failed(tmp_path):
    repo = FakeRepo([])
    report = run_task(_write_task(tmp_path), "developer", repo=repo,
                      make_branch=lambda b: None,
                      run_developer=lambda t, r: RunnerResult(ok=False, exit_code=1),
                      run_reviewer=lambda t, r: RunnerResult(True, 0), cap=2)
    assert report.status == "failed"
    assert report.rounds == 1
