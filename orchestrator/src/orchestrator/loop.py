# orchestrator/src/orchestrator/loop.py
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from orchestrator.branch import branch_name, derive_tagline
from orchestrator.seams import RunnerResult


@dataclass
class TaskReport:
    task_id: int
    branch: str
    status: str
    review_status: str
    review_notes: str | None
    rounds: int
    summary: str | None
    artifacts: list


def _report(repo, task_id: int, rounds: int) -> TaskReport:
    row = repo.get(task_id)
    return TaskReport(
        task_id=task_id, branch=row["branch"], status=row["status"],
        review_status=row["review_status"], review_notes=row["review_notes"],
        rounds=rounds, summary=row["summary"], artifacts=row["artifacts"],
    )


def run_task(task_file: str, role: str, *, repo,
             make_branch: Callable[[str], None],
             run_developer: Callable[[int, int], RunnerResult],
             run_reviewer: Callable[[int, int], RunnerResult],
             cap: int = 2) -> TaskReport:
    text = Path(task_file).read_text(encoding="utf-8")
    title_line = next((ln.strip()[2:] for ln in text.splitlines()
                       if ln.strip().startswith("# ")), Path(task_file).stem)
    tagline = derive_tagline(text, fallback=Path(task_file).stem)

    task_id = repo.create(spec_ref=task_file, title=title_line, assignee_role=role)
    branch = branch_name(task_id, tagline)
    make_branch(branch)
    repo.set_branch(task_id, branch)

    rounds = 0
    for round in range(1, cap + 1):
        rounds = round
        repo.set_round(task_id, round)

        dev = run_developer(task_id, round)
        if not dev.ok:
            repo.set_status(task_id, "failed")
            return _report(repo, task_id, rounds)

        rev = run_reviewer(task_id, round)
        if not rev.ok:
            repo.set_status(task_id, "failed")
            return _report(repo, task_id, rounds)

        if repo.get(task_id)["review_status"] == "approved":
            repo.set_status(task_id, "approved")
            return _report(repo, task_id, rounds)

    repo.set_status(task_id, "needs_changes")  # cap reached
    return _report(repo, task_id, rounds)
