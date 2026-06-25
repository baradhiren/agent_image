---
name: bootstrap
description: Use when the project's toolset changes or a build/test fails on a missing tool - re-runs the deterministic toolset bootstrap (mise install + reshim, then specs/toolset.yaml setup commands).
---

# Bootstrap the project toolset

Run this when:
- you edited the project's `.mise.toml` / `.tool-versions` or `specs/toolset.yaml`, or
- a build or test just failed because a runtime or dependency was missing.

Steps:
1. From the project root, run `bootstrap.sh`.
2. It installs the pinned runtimes via mise, puts them on `PATH` (`mise reshim`),
   then runs the ordered `setup:` commands from `specs/toolset.yaml`.
3. If a `setup:` command fails, bootstrap stops at it — fix that command (or the
   tool versions) and re-run.

This is idempotent: re-running with nothing changed is a fast no-op.
