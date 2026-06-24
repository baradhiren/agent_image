from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from memory.config import Settings

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "sql" / "001_schema.sql"


def connect(settings: Settings) -> psycopg.Connection:
    conn = psycopg.connect(settings.database_url, autocommit=True)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    register_vector(conn)
    return conn


def apply_schema(conn: psycopg.Connection, code_dim: int = 384, doc_dim: int = 384) -> None:
    sql = SCHEMA_FILE.read_text()
    sql = sql.replace(":CODE_DIM", str(code_dim)).replace(":DOC_DIM", str(doc_dim))
    conn.execute(sql)
