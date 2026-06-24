import subprocess
import sys

from memory.config import Settings
from memory.db import apply_schema, connect
from memory.repository import Repository

_SUPPORTED = (".py", ".md")


def enqueue_changed(root: str, commit_sha: str, rel_paths: list[str]) -> int:
    settings = Settings.from_env()
    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    count = 0
    for rel_path in rel_paths:
        if rel_path.endswith(_SUPPORTED):
            repo.enqueue(commit_sha, rel_path)
            count += 1
    return count


def changed_files(root: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", root, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line.endswith(_SUPPORTED)]


def head_sha(root: str) -> str:
    return subprocess.run(
        ["git", "-C", root, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(f"enqueued {enqueue_changed(root, head_sha(root), changed_files(root))} files")


if __name__ == "__main__":
    main()
