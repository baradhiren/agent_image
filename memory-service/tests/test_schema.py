def test_all_tables_exist(conn):
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {
        "files", "symbols", "edges", "code_chunks", "doc_chunks",
        "spec_links", "ingest_queue", "embedding_config",
    }.issubset(names)


def test_edges_have_resolution_columns(conn):
    cols = {
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='edges'"
        ).fetchall()
    }
    assert {"dst_symbol_id", "resolution", "dst_name"}.issubset(cols)
