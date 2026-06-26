"""Snapshot the memory DB to disk and restore it, guarded by a compatibility
check. The on-disk snapshot is a cache + a knowledge store: derivable data can
be rebuilt by reconcile, but agent-authored spec_links must never be lost."""
from __future__ import annotations

from datetime import datetime, timezone

from memory.config import Settings

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
