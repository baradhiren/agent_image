import sys
import time

from memory.config import Settings
from memory.db import apply_schema, connect
from memory.embeddings.factory import build_embedder
from memory.parser.python_parser import PythonParser
from memory.repository import Repository
from memory.worker import Worker


def _worker() -> tuple[Worker, str]:
    settings = Settings.from_env()
    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    repo.ensure_embedding_config("code", settings.code_embed.provider, settings.code_embed.model, settings.code_embed.dim)
    repo.ensure_embedding_config("doc", settings.doc_embed.provider, settings.doc_embed.model, settings.doc_embed.dim)
    worker = Worker(repo, build_embedder(settings.code_embed), build_embedder(settings.doc_embed), PythonParser())
    return worker, settings.database_url


def drain_once(root: str) -> dict:
    worker, _ = _worker()
    return worker.drain(root)


def serve(root: str, interval: float = 2.0) -> None:
    worker, _ = _worker()
    while True:
        worker.drain(root)
        time.sleep(interval)


if __name__ == "__main__":
    serve(sys.argv[1] if len(sys.argv) > 1 else "/project")
