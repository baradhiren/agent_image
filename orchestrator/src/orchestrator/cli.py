# orchestrator/src/orchestrator/cli.py
import argparse
import os
import sys
from pathlib import Path

from memory.config import Settings
from memory.db import connect
from memory.tasks import TaskRepository

from orchestrator import seams
from orchestrator.branch import checkout, create_task_branch, current_branch
from orchestrator.loop import TaskReport, run_task
from orchestrator.orchestrate import orchestrate

ROLES = ("developer",)  # the worker role; the reviewer pass is automatic
COMPOSE_DIR_DEFAULT = str(Path(__file__).resolve().parents[3])  # the agent_image repo root


def run_one_task(*, project_dir: str, role: str, task_file: str, compose_dir: str) -> TaskReport:
    base = current_branch(project_dir)

    def make_branch(branch: str) -> None:
        create_task_branch(project_dir, branch, base=base)

    def run_developer(task_id: int, round: int):
        return seams.exec_runner("developer", task_id, round,
                                 compose_dir=compose_dir, project_dir=project_dir)

    def run_reviewer(task_id: int, round: int):
        return seams.exec_runner("reviewer", task_id, round,
                                 compose_dir=compose_dir, project_dir=project_dir)

    def do_task() -> TaskReport:
        # Connect only after the stack is up (orchestrate calls this after `up`),
        # so the db on localhost:5432 is reachable.
        conn = connect(Settings.from_env())
        try:
            return run_task(task_file, role, repo=TaskRepository(conn),
                            make_branch=make_branch, run_developer=run_developer,
                            run_reviewer=run_reviewer)
        finally:
            conn.close()

    try:
        return orchestrate(
            run_task_fn=do_task,
            up=lambda: seams.compose_up(compose_dir=compose_dir, project_dir=project_dir),
            down=lambda: seams.compose_down(compose_dir=compose_dir, project_dir=project_dir),
            dump=lambda: seams.snapshot_dump(compose_dir=compose_dir, project_dir=project_dir),
        )
    finally:
        # Leave the user's working branch untouched: the task work stays on its
        # own branch; restore the base branch they started on. Best-effort.
        try:
            checkout(project_dir, base)
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentctl")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one task through the dev->review loop")
    run.add_argument("--role", required=True, choices=ROLES)
    run.add_argument("task_file")

    ns = parser.parse_args(argv)  # argv=None reads sys.argv (incl. the `run` subcommand)

    project_dir = os.getcwd()
    compose_dir = os.environ.get("AGENT_IMAGE_DIR", COMPOSE_DIR_DEFAULT)
    report = run_one_task(project_dir=project_dir, role=ns.role,
                          task_file=ns.task_file, compose_dir=compose_dir)
    print(f"task #{report.task_id}: {report.status}")
    print(f"  branch:        {report.branch}")
    print(f"  review:        {report.review_status} (rounds: {report.rounds})")
    if report.review_notes:
        print(f"  review notes:  {report.review_notes}")
    if report.summary:
        print(f"  summary:       {report.summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
