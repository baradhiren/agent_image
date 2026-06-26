from memory.config import Settings
from memory.db import apply_schema, connect, reset_db


def test_reset_db_drops_all_tables():
    conn = connect(Settings.from_env())
    apply_schema(conn)  # creates files, symbols, ... tables
    conn.execute("INSERT INTO files (path, language, content_hash) VALUES ('x.py','python','h')")
    assert conn.execute("SELECT count(*) FROM files").fetchone()[0] == 1

    reset_db(conn)

    remaining = conn.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'public'"
    ).fetchone()[0]
    conn.close()
    assert remaining == 0
