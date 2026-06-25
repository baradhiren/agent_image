"""What the ingestion pipeline considers a source file.

Centralizes the two rules shared by the full-scan reconcile path and the
git-hook enqueue path so they can't drift: which extensions we ingest
(derived from the parser registry + docs) and which directories we never
descend into (VCS internals, dependency trees, virtualenvs, caches, build
output). Keeping these here is what stops a reconcile from chewing through a
project's ``.venv`` / ``node_modules``.
"""

import os

from memory.parser.registry import code_extensions

DOC_EXTENSIONS: frozenset[str] = frozenset({".md"})
SUPPORTED_EXTENSIONS: frozenset[str] = code_extensions() | DOC_EXTENSIONS

IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",
        ".venv", "venv", "site-packages",
        "node_modules", "bower_components",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".cache",
        "dist", "build", "target", ".next", ".nuxt", ".svelte-kit",
        ".eggs", ".idea", ".vscode", "coverage", ".gradle",
    }
)


def is_supported(rel_path: str) -> bool:
    return os.path.splitext(rel_path)[1] in SUPPORTED_EXTENSIONS


def _ignored_dir(name: str) -> bool:
    return name in IGNORE_DIRS or name.endswith(".egg-info")


def iter_source_files(root: str) -> list[str]:
    """Sorted relative paths of supported source files under ``root``.

    Prunes ``IGNORE_DIRS`` in place during the walk, so we never even descend
    into dependency/build trees.
    """
    results: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _ignored_dir(d)]
        for name in filenames:
            if os.path.splitext(name)[1] in SUPPORTED_EXTENSIONS:
                results.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(results)
