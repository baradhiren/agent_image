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
