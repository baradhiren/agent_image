"""One-shot startup: enforce isolation (reset), pick the snapshot home (probe +
fallback), restore-or-seed, then catch up with an incremental reconcile."""
from __future__ import annotations

import os
import sys

from memory import reconcile, snapshot
from memory.config import Settings
from memory.db import connect, reset_db


def snapshot_home(project_dir: str, fallback_dir: str = "/agent-memory") -> tuple[str, str]:
    """Return (home_dir, location). Prefer PROJECT_DIR/.agent-memory; on an
    unwritable target fall back to the named-volume mount. Never lossy."""
    colocated = os.path.join(project_dir, ".agent-memory")
    try:
        os.makedirs(colocated, exist_ok=True)
        probe = os.path.join(colocated, ".write-probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return colocated, "co-located"
    except OSError:
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir, "fallback-volume"


def run_startup(root: str, settings: Settings) -> dict:
    home, location = snapshot_home(root)
    if location == "fallback-volume":
        print(
            "WARNING: PROJECT_DIR/.agent-memory is not writable; memory will NOT "
            "co-locate with the source. Persisting to the named volume instead.",
            file=sys.stderr,
        )

    # Isolation: the snapshot (or a fresh seed), not the previous volume, is the
    # source of truth on every start.
    conn = connect(settings)
    reset_db(conn)
    conn.close()

    restored = snapshot.restore(home, settings)

    # Always catch up. reconcile applies the schema + seeds when the DB is empty
    # (no restore), and re-embeds only changed files when restored (hash-diff).
    summary = reconcile.run(root, settings)
    return {"location": location, "restored": restored, "reconcile": summary}


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "/project"
    print(run_startup(root, Settings.from_env()))


if __name__ == "__main__":
    main()
