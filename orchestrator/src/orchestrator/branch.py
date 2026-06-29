import re
import subprocess

_MAX_TAGLINE = 40


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) > _MAX_TAGLINE:
        s = s[:_MAX_TAGLINE].rstrip("-")
    return s


def derive_tagline(task_text: str, fallback: str) -> str:
    for line in task_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            slug = _slug(stripped[2:])
            if slug:
                return slug
    return _slug(fallback)


def branch_name(task_id: int, tagline: str) -> str:
    return f"feat/{task_id}-{tagline}"


def _git(workspace: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", workspace, *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def current_branch(workspace: str) -> str:
    return _git(workspace, "rev-parse", "--abbrev-ref", "HEAD")


def create_task_branch(workspace: str, branch: str, base: str) -> None:
    _git(workspace, "checkout", "-b", branch, base)


def checkout(workspace: str, ref: str) -> None:
    _git(workspace, "checkout", ref)


def diff_summary(workspace: str, base: str, branch: str) -> str:
    return _git(workspace, "diff", "--stat", f"{base}..{branch}")
