import pytest

from memory.repository import EmbeddingConfigMismatch, Repository


def test_provider_only_change_updates_not_raises(conn):
    repo = Repository(conn)
    repo.ensure_embedding_config("code", "local", "bge", 384)
    repo.ensure_embedding_config("code", "remote", "bge", 384)  # provider-only: no raise
    assert repo.get_embedding_config("code") == {"provider": "remote", "model": "bge", "dim": 384}


def test_model_or_dim_change_still_raises(conn):
    repo = Repository(conn)
    repo.ensure_embedding_config("code", "local", "bge", 384)
    with pytest.raises(EmbeddingConfigMismatch):
        repo.ensure_embedding_config("code", "local", "bge", 768)    # dim differs
    with pytest.raises(EmbeddingConfigMismatch):
        repo.ensure_embedding_config("code", "local", "other", 384)  # model differs
