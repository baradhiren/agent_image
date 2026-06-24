import psycopg

from memory.models import ParsedFile


class EmbeddingConfigMismatch(Exception):
    pass


class Repository:
    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def upsert_file_row(self, path: str, language: str, content_hash: str) -> int:
        return self._conn.execute(
            "INSERT INTO files (path, language, content_hash) VALUES (%s, %s, %s) "
            "ON CONFLICT (path) DO UPDATE SET language = EXCLUDED.language, "
            "content_hash = EXCLUDED.content_hash RETURNING id",
            (path, language, content_hash),
        ).fetchone()[0]

    def file_hash(self, path: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM files WHERE path = %s", (path,)
        ).fetchone()
        return row[0] if row else None

    def delete_file(self, path: str) -> None:
        self._conn.execute("DELETE FROM files WHERE path = %s", (path,))

    def replace_structure(self, file_id: int, parsed: ParsedFile) -> None:
        self._conn.execute("DELETE FROM symbols WHERE file_id = %s", (file_id,))
        self._conn.execute("DELETE FROM edges WHERE file_id = %s", (file_id,))
        for s in parsed.symbols:
            self._conn.execute(
                "INSERT INTO symbols (file_id, qualname, name, kind, start_line, end_line) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (file_id, s.qualname, s.name, s.kind, s.start_line, s.end_line),
            )
        for e in parsed.edges:
            self._conn.execute(
                "INSERT INTO edges (file_id, src_qualname, dst_name, kind, resolution) "
                "VALUES (%s, %s, %s, %s, 'pending')",
                (file_id, e.src_qualname, e.dst_name, e.kind),
            )

    def list_db_files(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT path FROM files ORDER BY path").fetchall()]

    def resolve_pending_edges(self) -> None:
        by_name: dict[str, list[int]] = {}
        for name, sid in self._conn.execute("SELECT name, id FROM symbols").fetchall():
            by_name.setdefault(name, []).append(sid)
        pending = self._conn.execute(
            "SELECT id, dst_name FROM edges "
            "WHERE kind = 'calls' AND (resolution = 'pending' OR dst_symbol_id IS NULL)"
        ).fetchall()
        for edge_id, dst_name in pending:
            matches = by_name.get(dst_name, [])
            if len(matches) == 1:
                self._conn.execute(
                    "UPDATE edges SET dst_symbol_id = %s, resolution = 'resolved' WHERE id = %s",
                    (matches[0], edge_id),
                )
            else:
                resolution = "ambiguous" if len(matches) > 1 else "external"
                self._conn.execute(
                    "UPDATE edges SET dst_symbol_id = NULL, resolution = %s WHERE id = %s",
                    (resolution, edge_id),
                )

    def reresolve_all_edges(self) -> None:
        self._conn.execute(
            "UPDATE edges SET dst_symbol_id = NULL, resolution = 'pending' WHERE kind = 'calls'"
        )
        self.resolve_pending_edges()

    def impact_of(self, qualname: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT DISTINCT e.src_qualname, f.path FROM edges e "
            "JOIN files f ON f.id = e.file_id "
            "WHERE e.kind = 'calls' AND e.dst_symbol_id IN "
            "(SELECT id FROM symbols WHERE qualname = %s)",
            (qualname,),
        ).fetchall()
        return [{"src_qualname": r[0], "path": r[1]} for r in rows]

    def sync_code_chunks(self, file_id: int, chunks, embedder) -> int:
        existing = {
            r[0]: r[1]
            for r in self._conn.execute(
                "SELECT chunk_key, content_hash FROM code_chunks WHERE file_id = %s", (file_id,)
            ).fetchall()
        }
        to_embed = [c for c in chunks if existing.get(c.chunk_key) != c.content_hash]
        vectors = embedder.embed([c.text for c in to_embed]) if to_embed else []
        vec_by_key = {c.chunk_key: v for c, v in zip(to_embed, vectors)}
        for c in chunks:
            if c.chunk_key in vec_by_key:
                self._conn.execute(
                    "INSERT INTO code_chunks (file_id, chunk_key, qualname, content_hash, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (file_id, chunk_key) DO UPDATE SET qualname = EXCLUDED.qualname, "
                    "content_hash = EXCLUDED.content_hash, text = EXCLUDED.text, embedding = EXCLUDED.embedding",
                    (file_id, c.chunk_key, c.qualname, c.content_hash, c.text, vec_by_key[c.chunk_key]),
                )
        keys = [c.chunk_key for c in chunks]
        if keys:
            self._conn.execute(
                "DELETE FROM code_chunks WHERE file_id = %s AND chunk_key <> ALL(%s)", (file_id, keys)
            )
        else:
            self._conn.execute("DELETE FROM code_chunks WHERE file_id = %s", (file_id,))
        return len(to_embed)

    def sync_doc_chunks(self, path: str, chunks, embedder) -> int:
        existing = {
            r[0]: r[1]
            for r in self._conn.execute(
                "SELECT chunk_key, content_hash FROM doc_chunks WHERE path = %s", (path,)
            ).fetchall()
        }
        to_embed = [c for c in chunks if existing.get(c.chunk_key) != c.content_hash]
        vectors = embedder.embed([c.text for c in to_embed]) if to_embed else []
        vec_by_key = {c.chunk_key: v for c, v in zip(to_embed, vectors)}
        for c in chunks:
            if c.chunk_key in vec_by_key:
                self._conn.execute(
                    "INSERT INTO doc_chunks (path, chunk_key, content_hash, text, embedding) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (path, chunk_key) DO UPDATE SET content_hash = EXCLUDED.content_hash, "
                    "text = EXCLUDED.text, embedding = EXCLUDED.embedding",
                    (path, c.chunk_key, c.content_hash, c.text, vec_by_key[c.chunk_key]),
                )
        keys = [c.chunk_key for c in chunks]
        if keys:
            self._conn.execute("DELETE FROM doc_chunks WHERE path = %s AND chunk_key <> ALL(%s)", (path, keys))
        else:
            self._conn.execute("DELETE FROM doc_chunks WHERE path = %s", (path,))
        return len(to_embed)

    def search_code(self, query_embedding, k: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT c.qualname, f.path, c.text FROM code_chunks c "
            "JOIN files f ON f.id = c.file_id ORDER BY c.embedding <=> %s::vector LIMIT %s",
            (query_embedding, k),
        ).fetchall()
        return [{"qualname": r[0], "path": r[1], "text": r[2]} for r in rows]

    def search_docs(self, query_embedding, k: int = 5) -> list[dict]:
        rows = self._conn.execute(
            "SELECT path, text FROM doc_chunks ORDER BY embedding <=> %s::vector LIMIT %s",
            (query_embedding, k),
        ).fetchall()
        return [{"path": r[0], "text": r[1]} for r in rows]

    def get_symbol(self, qualname: str) -> dict | None:
        row = self._conn.execute(
            "SELECT s.qualname, s.name, s.kind, f.path, s.start_line, s.end_line "
            "FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.qualname = %s LIMIT 1",
            (qualname,),
        ).fetchone()
        if not row:
            return None
        return {"qualname": row[0], "name": row[1], "kind": row[2],
                "path": row[3], "start_line": row[4], "end_line": row[5]}

    def add_spec_link(self, spec_path: str, symbol_qualname: str) -> None:
        self._conn.execute(
            "INSERT INTO spec_links (spec_path, symbol_qualname) VALUES (%s, %s)",
            (spec_path, symbol_qualname),
        )

    def spec_for(self, qualname: str) -> list[str]:
        return [
            r[0]
            for r in self._conn.execute(
                "SELECT spec_path FROM spec_links WHERE symbol_qualname = %s ORDER BY spec_path",
                (qualname,),
            ).fetchall()
        ]

    def prune_spec_links(self) -> int:
        return self._conn.execute(
            "DELETE FROM spec_links WHERE symbol_qualname NOT IN (SELECT qualname FROM symbols)"
        ).rowcount

    def enqueue(self, commit_sha: str, rel_path: str) -> None:
        self._conn.execute(
            "INSERT INTO ingest_queue (commit_sha, rel_path) VALUES (%s, %s)", (commit_sha, rel_path)
        )

    def dequeue_pending(self, limit: int = 100) -> list[tuple[int, str]]:
        return [
            (r[0], r[1])
            for r in self._conn.execute(
                "SELECT id, rel_path FROM ingest_queue WHERE status = 'pending' ORDER BY id LIMIT %s",
                (limit,),
            ).fetchall()
        ]

    def mark_done(self, ids: list[int]) -> None:
        if ids:
            self._conn.execute("UPDATE ingest_queue SET status = 'done' WHERE id = ANY(%s)", (ids,))

    def get_embedding_config(self, collection: str) -> dict | None:
        row = self._conn.execute(
            "SELECT provider, model, dim FROM embedding_config WHERE collection = %s", (collection,)
        ).fetchone()
        return None if not row else {"provider": row[0], "model": row[1], "dim": row[2]}

    def ensure_embedding_config(self, collection: str, provider: str, model: str, dim: int) -> None:
        existing = self.get_embedding_config(collection)
        wanted = {"provider": provider, "model": model, "dim": dim}
        if existing is None:
            self._conn.execute(
                "INSERT INTO embedding_config (collection, provider, model, dim) VALUES (%s, %s, %s, %s)",
                (collection, provider, model, dim),
            )
        elif existing != wanted:
            raise EmbeddingConfigMismatch(
                f"{collection}: stored {existing} != configured {wanted}; reconcile/re-embed required"
            )
