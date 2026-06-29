import json

import psycopg

_COLUMNS = (
    "id, spec_ref, title, assignee_role, branch, status, round, "
    "review_status, review_notes, summary, artifacts"
)


class TaskRepository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def create(self, spec_ref: str, title: str, assignee_role: str) -> int:
        return self._conn.execute(
            "INSERT INTO tasks (spec_ref, title, assignee_role) VALUES (%s, %s, %s) "
            "RETURNING id",
            (spec_ref, title, assignee_role),
        ).fetchone()[0]

    def set_branch(self, task_id: int, branch: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET branch = %s, updated_at = now() WHERE id = %s",
            (branch, task_id),
        )

    def set_round(self, task_id: int, round: int) -> None:
        self._conn.execute(
            "UPDATE tasks SET round = %s, updated_at = now() WHERE id = %s",
            (round, task_id),
        )

    def record_developer_result(self, task_id: int, summary: str, artifacts: list[str]) -> None:
        self._conn.execute(
            "UPDATE tasks SET summary = %s, artifacts = %s, updated_at = now() WHERE id = %s",
            (summary, json.dumps(artifacts), task_id),
        )

    def record_review(self, task_id: int, review_status: str, review_notes: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET review_status = %s, review_notes = %s, updated_at = now() "
            "WHERE id = %s",
            (review_status, review_notes, task_id),
        )

    def set_status(self, task_id: int, status: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET status = %s, updated_at = now() WHERE id = %s",
            (status, task_id),
        )

    def get(self, task_id: int) -> dict | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM tasks WHERE id = %s", (task_id,)
        ).fetchone()
        if row is None:
            return None
        keys = [c.strip() for c in _COLUMNS.split(",")]
        return dict(zip(keys, row))
