from memory.config import EmbedConfig
from memory.embeddings.base import EmbeddingProvider
from memory.embeddings.local import LocalEmbeddingProvider
from memory.embeddings.remote import RemoteEmbeddingProvider


def build_embedder(cfg: EmbedConfig) -> EmbeddingProvider:
    if cfg.provider == "remote":
        if not cfg.url:
            raise ValueError("remote embedder requires a *_EMBED_URL")
        return RemoteEmbeddingProvider(cfg.url, cfg.dim)
    return LocalEmbeddingProvider(cfg.model)
