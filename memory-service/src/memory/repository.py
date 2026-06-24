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
