# memory-service/src/memory/agent_runner.py
"""Generic role runner exec'd into a warm agent container. Drives a Claude Agent
SDK session (lazy import) and writes the result to the task's row. Role-branched:
developer commits + records summary/artifacts; reviewer records a verdict."""
import argparse
import os
import re
import subprocess
import sys

from memory.config import Settings
from memory.db import connect
from memory.tasks import TaskRepository

_VERDICT = re.compile(r"^VERDICT:\s*(approved|needs_changes)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_verdict(text: str) -> tuple[str, str]:
    matches = _VERDICT.findall(text)
    status = matches[-1].lower() if matches else "needs_changes"
    return status, text


def build_prompt(role: str, task_text: str, review_notes: str | None, branch: str) -> str:
    if role == "developer":
        prompt = (
            f"Implement this task. Work on the current git branch ({branch}) and "
            f"commit your changes when done.\n\n--- TASK ---\n{task_text}\n"
        )
        if review_notes:
            prompt += f"\n--- REVIEWER NOTES FROM THE PREVIOUS ROUND ---\n{review_notes}\n"
        return prompt
    return (
        f"Review the changes on branch {branch} against the task below. Inspect the "
        f"diff and the code. End your response with a single line exactly "
        f"'VERDICT: approved' or 'VERDICT: needs_changes', followed by your notes.\n\n"
        f"--- TASK ---\n{task_text}\n"
    )


def run(role: str, task_id: int, round: int, *, repo, workspace: str,
        run_session, head_sha) -> None:
    row = repo.get(task_id)
    task_text = _read_task_text(workspace, row["spec_ref"])
    prompt = build_prompt(role, task_text, row["review_notes"], row["branch"])
    output = run_session(prompt)
    if role == "developer":
        repo.record_developer_result(task_id, summary=output, artifacts=[head_sha(workspace)])
    else:
        status, notes = parse_verdict(output)
        repo.record_review(task_id, review_status=status, review_notes=notes)


def _read_task_text(workspace: str, spec_ref: str) -> str:
    path = spec_ref if os.path.isabs(spec_ref) else os.path.join(workspace, spec_ref)
    with open(path, encoding="utf-8") as f:
        return f.read()


def _head_sha(workspace: str) -> str:
    return subprocess.run(
        ["git", "-C", workspace, "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _run_session(prompt: str) -> str:
    """Run one Claude Agent SDK session under Python control; return the final
    assistant text. SDK imported lazily so this module loads without the SDK."""
    import asyncio

    from claude_agent_sdk import (
        AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query,
    )

    options = ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code"},
        cwd=os.environ.get("WORKSPACE", "/workspace"),
        permission_mode="bypassPermissions",
        model=os.environ.get("AGENT_MODEL", "claude-opus-4-8"),
    )

    async def _go() -> str:
        final = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        final = block.text
            elif isinstance(message, ResultMessage):
                break
        return final

    return asyncio.run(_go())


def main() -> int:
    parser = argparse.ArgumentParser(prog="memory.agent_runner")
    parser.add_argument("--role", required=True, choices=("developer", "reviewer"))
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--round", type=int, required=True)
    ns = parser.parse_args()

    conn = connect(Settings.from_env())
    try:
        run(ns.role, ns.task_id, ns.round, repo=TaskRepository(conn),
            workspace=os.environ.get("WORKSPACE", "/workspace"),
            run_session=_run_session, head_sha=_head_sha)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
