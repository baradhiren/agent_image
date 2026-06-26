"""One-shot startup: enforce isolation (reset), pick the snapshot home (probe +
fallback), restore-or-seed, then catch up with an incremental reconcile."""
from __future__ import annotations

import os
import sys


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
