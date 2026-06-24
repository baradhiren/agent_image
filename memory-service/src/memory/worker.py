import hashlib
from pathlib import Path

from memory.chunking import chunk_code, chunk_docs
from memory.embeddings.base import EmbeddingProvider
from memory.parser.base import LanguageParser
from memory.repository import Repository


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()


class Worker:
    def __init__(
        self,
        repo: Repository,
        code_embedder: EmbeddingProvider,
        doc_embedder: EmbeddingProvider,
        parser: LanguageParser,
    ) -> None:
        self._repo = repo
        self._code_embedder = code_embedder
        self._doc_embedder = doc_embedder
        self._parser = parser

    def process_file(self, root: str, rel_path: str) -> str:
        abs_path = Path(root) / rel_path
        if not abs_path.exists():
            self._repo.delete_file(rel_path)
            self._repo.sync_doc_chunks(rel_path, [], self._doc_embedder)
            return "deleted"

        text = abs_path.read_text()
        digest = content_hash(text)
        if self._repo.file_hash(rel_path) == digest:
            return "skipped"

        if rel_path.endswith(".py"):
            parsed = self._parser.parse(rel_path, text)
            file_id = self._repo.upsert_file_row(rel_path, "python", digest)
            self._repo.replace_structure(file_id, parsed)
            self._repo.resolve_pending_edges()  # closure
            self._repo.sync_code_chunks(file_id, chunk_code(parsed), self._code_embedder)
            return "ingested"

        if rel_path.endswith(".md"):
            self._repo.upsert_file_row(rel_path, "markdown", digest)
            self._repo.sync_doc_chunks(rel_path, chunk_docs(rel_path, text), self._doc_embedder)
            return "ingested"

        return "skipped"

    def drain(self, repo_root: str) -> dict:
        pending = self._repo.dequeue_pending()
        result: dict = {"ingested": [], "skipped": [], "deleted": []}
        seen: set[str] = set()
        ids: list[int] = []
        for queue_id, rel_path in pending:
            ids.append(queue_id)
            if rel_path in seen:
                continue
            seen.add(rel_path)
            result.setdefault(self.process_file(repo_root, rel_path), []).append(rel_path)
        self._repo.mark_done(ids)
        return result
