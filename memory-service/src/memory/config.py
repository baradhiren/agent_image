import os
from dataclasses import dataclass


@dataclass(frozen=True)
class EmbedConfig:
    provider: str       # "local" | "remote"
    model: str
    dim: int
    url: str | None


@dataclass(frozen=True)
class Settings:
    database_url: str
    code_embed: EmbedConfig
    doc_embed: EmbedConfig

    @classmethod
    def from_env(cls) -> "Settings":
        def embed(prefix: str) -> EmbedConfig:
            return EmbedConfig(
                provider=os.environ.get(f"{prefix}_EMBED_PROVIDER", "local"),
                model=os.environ.get(f"{prefix}_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
                dim=int(os.environ.get(f"{prefix}_EMBED_DIM", "384")),
                url=os.environ.get(f"{prefix}_EMBED_URL"),
            )

        return cls(
            database_url=os.environ.get(
                "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/memory"
            ),
            code_embed=embed("CODE"),
            doc_embed=embed("DOC"),
        )
