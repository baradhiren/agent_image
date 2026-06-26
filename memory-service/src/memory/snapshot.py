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
from memory.db import connect

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
        with open(meta_tmp, "w") as f:
            json.dump(meta, f, indent=2)
        os.replace(meta_tmp, str(target / "meta.json"))
    except BaseException:
        if os.path.exists(meta_tmp):
            os.remove(meta_tmp)
        raise
