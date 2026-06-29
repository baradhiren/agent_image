import os
import subprocess
from dataclasses import dataclass

RUNNER_PYTHON = "/opt/memory/.venv/bin/python"


@dataclass(frozen=True)
class RunnerResult:
    ok: bool
    exit_code: int


def _env(project_dir: str) -> dict:
    return {**os.environ, "PROJECT_DIR": project_dir}


def exec_runner(role: str, task_id: int, round: int, *, compose_dir: str,
                project_dir: str) -> RunnerResult:
    cmd = [
        "docker", "compose", "exec", "-T", role,
        RUNNER_PYTHON, "-m", "memory.agent_runner",
        "--role", role, "--task-id", str(task_id), "--round", str(round),
    ]
    proc = subprocess.run(cmd, cwd=compose_dir, env=_env(project_dir))
    return RunnerResult(ok=proc.returncode == 0, exit_code=proc.returncode)


def compose_up(*, compose_dir: str, project_dir: str) -> None:
    subprocess.run(
        ["docker", "compose", "up", "-d", "--wait"],
        cwd=compose_dir, env=_env(project_dir), check=True,
    )


def compose_down(*, compose_dir: str, project_dir: str) -> None:
    subprocess.run(
        ["docker", "compose", "down"],
        cwd=compose_dir, env=_env(project_dir), check=True,
    )


def snapshot_dump(*, compose_dir: str, project_dir: str) -> None:
    # Run the Q1 dump in a one-shot init container (it mounts /project read-write
    # and carries the pg18 client). Raises CalledProcessError on failure.
    subprocess.run(
        ["docker", "compose", "run", "--rm", "--no-deps", "init",
         "uv", "run", "python", "-m", "memory.snapshot", "dump",
         "/project/.agent-memory"],
        cwd=compose_dir, env=_env(project_dir), check=True,
    )
