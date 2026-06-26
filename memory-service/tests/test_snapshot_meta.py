from datetime import datetime

from memory.config import EmbedConfig, Settings
from memory import snapshot


def _settings(code_model="BAAI/bge-small-en-v1.5", code_dim=384,
              doc_model="BAAI/bge-small-en-v1.5", doc_dim=384) -> Settings:
    return Settings(
        database_url="postgresql://x/y",
        code_embed=EmbedConfig("remote", code_model, code_dim, "http://e:80"),
        doc_embed=EmbedConfig("remote", doc_model, doc_dim, "http://e:80"),
    )


def test_build_meta_has_required_fields():
    meta = snapshot.build_meta(_settings(), pg_major=18,
                               source_head="abc123", location="co-located")
    assert meta["schema_version"] == snapshot.SCHEMA_VERSION
    assert meta["pg_major"] == 18
    assert meta["code_embed"] == {"model": "BAAI/bge-small-en-v1.5", "dim": 384}
    assert meta["doc_embed"] == {"model": "BAAI/bge-small-en-v1.5", "dim": 384}
    assert meta["source_head"] == "abc123"
    assert meta["location"] == "co-located"
    # created_at is a parseable ISO-8601 timestamp
    datetime.fromisoformat(meta["created_at"])


def test_compatible_when_everything_matches():
    s = _settings()
    meta = snapshot.build_meta(s, 18, None, "co-located")
    assert snapshot.meta_is_compatible(meta, s, pg_major=18) is True


def test_incompatible_on_dim_mismatch():
    meta = snapshot.build_meta(_settings(code_dim=384), 18, None, "co-located")
    assert snapshot.meta_is_compatible(meta, _settings(code_dim=768), 18) is False


def test_incompatible_on_model_mismatch():
    meta = snapshot.build_meta(_settings(doc_model="m-a"), 18, None, "co-located")
    assert snapshot.meta_is_compatible(meta, _settings(doc_model="m-b"), 18) is False


def test_incompatible_on_pg_major_mismatch():
    s = _settings()
    meta = snapshot.build_meta(s, 18, None, "co-located")
    assert snapshot.meta_is_compatible(meta, s, pg_major=17) is False


def test_incompatible_on_schema_version_mismatch():
    s = _settings()
    meta = snapshot.build_meta(s, 18, None, "co-located")
    meta["schema_version"] = 999
    assert snapshot.meta_is_compatible(meta, s, 18) is False


def test_incompatible_on_garbage_meta():
    assert snapshot.meta_is_compatible({}, _settings(), 18) is False
    assert snapshot.meta_is_compatible({"code_embed": None}, _settings(), 18) is False
