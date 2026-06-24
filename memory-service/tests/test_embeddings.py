import pytest

from memory.config import EmbedConfig
from memory.embeddings import remote as remote_mod
from memory.embeddings.factory import build_embedder
from memory.embeddings.local import LocalEmbeddingProvider
from memory.embeddings.remote import RemoteEmbeddingProvider


def test_local_dim_and_embed():
    p = LocalEmbeddingProvider()
    assert p.dim == 384
    vectors = p.embed(["def foo(): pass", "a paragraph"])
    assert len(vectors) == 2 and all(len(v) == 384 for v in vectors)
    assert p.embed([]) == []


def test_remote_posts_to_tei(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return [[0.0] * 384]

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return FakeResp()

    monkeypatch.setattr(remote_mod.httpx, "post", fake_post)
    p = RemoteEmbeddingProvider("http://embeddings:80", dim=384)
    assert p.dim == 384
    assert p.embed([]) == []
    assert p.embed(["hi"]) == [[0.0] * 384]
    assert captured["url"] == "http://embeddings:80/embed"
    assert captured["json"] == {"inputs": ["hi"]}


def test_factory_selects_provider():
    assert isinstance(
        build_embedder(EmbedConfig("local", "BAAI/bge-small-en-v1.5", 384, None)),
        LocalEmbeddingProvider,
    )
    assert isinstance(
        build_embedder(EmbedConfig("remote", "x", 384, "http://embeddings:80")),
        RemoteEmbeddingProvider,
    )
    with pytest.raises(ValueError):
        build_embedder(EmbedConfig("remote", "x", 384, None))
