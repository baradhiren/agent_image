import subprocess
import sys


def _run(args, env_extra=None):
    import os
    # pytest runs from memory-service/; src/ holds the `memory` package.
    env = dict(os.environ, PYTHONPATH="src")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "memory.snapshot", *args],
        capture_output=True, text=True, env=env,
    )


def test_cli_dump_then_restore_roundtrip(tmp_path):
    dump = _run(["dump", str(tmp_path)])
    assert dump.returncode == 0, dump.stderr
    assert (tmp_path / "snapshot.dump").exists()

    restore = _run(["restore", str(tmp_path)])
    assert restore.returncode == 0, restore.stderr


def test_cli_dump_exits_nonzero_on_failure(tmp_path):
    bad = "postgresql://postgres:postgres@localhost:5432/nope_no_db"
    result = _run(["dump", str(tmp_path)], env_extra={"DATABASE_URL": bad})
    assert result.returncode != 0
