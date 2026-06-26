"""Snapshot the memory DB to disk and restore it, guarded by a compatibility
check. The on-disk snapshot is a cache + a knowledge store: derivable data can
be rebuilt by reconcile, but agent-authored spec_links must never be lost."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from memory.config import Settings
from memory.db import connect, reset_db

SCHEMA_VERSION = 1


def build_meta(settings: Settings, pg_major: int, source_head: str | None,
               location: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "pg_major": pg_major,
        "code_embed": {"model": settings.code_embed.model, "dim": settings.code_embed.dim},
        "doc_embed": {"model": settings.doc_embed.model, "dim": settings.doc_embed.dim},
        "source_head": source_head,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "location": location,
    }


def meta_is_compatible(meta: dict, settings: Settings, pg_major: int) -> bool:
    try:
        if meta.get("schema_version") != SCHEMA_VERSION:
            return False
        if meta.get("pg_major") != pg_major:
            return False
        for key, cfg in (("code_embed", settings.code_embed),
                         ("doc_embed", settings.doc_embed)):
            block = meta.get(key) or {}
            if block.get("model") != cfg.model or block.get("dim") != cfg.dim:
                return False
        return True
    except (AttributeError, TypeError):
        return False


def server_pg_major(conn) -> int:
    row = conn.execute("SHOW server_version_num").fetchone()
    return int(row[0]) // 10000


def _ensure_gitignore(target: Path) -> None:
    gi = target / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n")


def dump(target_dir: str, settings: Settings, source_head: str | None = None,
         location: str = "co-located") -> None:
    """Atomically write snapshot.dump + meta.json into target_dir, creating the
    self-ignoring .gitignore if missing. Raises on any failure so the caller can
    refuse to tear down on a failed dump."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    _ensure_gitignore(target)

    conn = connect(settings)
    try:
        pg_major = server_pg_major(conn)
    finally:
        conn.close()

    fd, tmp = tempfile.mkstemp(dir=str(target), suffix=".dump.tmp")
    os.close(fd)
    try:
        subprocess.run(
            ["pg_dump", "--format=custom", "--dbname", settings.database_url,
             "--file", tmp],
            check=True,
        )
        os.replace(tmp, str(target / "snapshot.dump"))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise

    meta = build_meta(settings, pg_major, source_head, location)
    meta_tmp = str(target / "meta.json.tmp")
    try:
        with open(meta_tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        os.replace(meta_tmp, str(target / "meta.json"))
    except BaseException:
        if os.path.exists(meta_tmp):
            os.remove(meta_tmp)
        raise


def restore(source_dir: str, settings: Settings) -> bool:
    """Restore a compatible snapshot into a clean DB. Returns True on success;
    False (caller then seeds) if the snapshot is missing, incompatible, or
    pg_restore fails. Never raises on the seed-fallback paths — derivable data
    is safe to rebuild."""
    source = Path(source_dir)
    dump_path = source / "snapshot.dump"
    meta_path = source / "meta.json"
    if not dump_path.exists() or not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False

    conn = connect(settings)
    try:
        if not meta_is_compatible(meta, settings, server_pg_major(conn)):
            return False
        reset_db(conn)  # clean slate before restore
    finally:
        conn.close()

    try:
        subprocess.run(
            ["pg_restore", "--no-owner", "--dbname", settings.database_url,
             str(dump_path)],
            check=True,
        )
    except subprocess.CalledProcessError:
        return False
    return True


def _git_head(repo_dir: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main() -> None:
    import sys

    if len(sys.argv) < 3 or sys.argv[1] not in ("dump", "restore"):
        print("usage: python -m memory.snapshot dump|restore <dir>", file=sys.stderr)
        raise SystemExit(2)

    action, target = sys.argv[1], sys.argv[2]
    settings = Settings.from_env()

    if action == "dump":
        dump(target, settings, source_head=_git_head(os.path.dirname(os.path.abspath(target))))
        print(f"dumped snapshot to {target}")
    else:
        ok = restore(target, settings)
        print("restored from snapshot" if ok else "no compatible snapshot; caller should seed")


if __name__ == "__main__":
    main()
