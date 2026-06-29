import json

from memory.config import Settings
from memory.db import apply_schema, connect, reset_db
from memory import snapshot


def _seed_and_dump(tmp_path):
    settings = Settings.from_env()
    conn = connect(settings)
    reset_db(conn)
    apply_schema(conn)
    conn.execute("INSERT INTO files (path, language, content_hash) VALUES ('a.py','python','h1')")
    conn.execute("INSERT INTO spec_links (spec_path, symbol_qualname) VALUES ('spec.md','a.fn')")
    conn.close()
    snapshot.dump(str(tmp_path), settings)
    return settings


def _counts(settings):
    conn = connect(settings)
    files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    links = conn.execute("SELECT count(*) FROM spec_links").fetchone()[0]
    conn.close()
    return files, links


def test_roundtrip_preserves_knowledge(tmp_path):
    settings = _seed_and_dump(tmp_path)
    # wipe, then restore
    conn = connect(settings); reset_db(conn); conn.close()

    assert snapshot.restore(str(tmp_path), settings) is True
    assert _counts(settings) == (1, 1)  # files AND spec_links survive


def test_restore_returns_false_when_no_snapshot(tmp_path):
    settings = Settings.from_env()
    assert snapshot.restore(str(tmp_path), settings) is False


def test_restore_refuses_incompatible_meta(tmp_path):
    settings = _seed_and_dump(tmp_path)
    meta = json.loads((tmp_path / "meta.json").read_text())
    meta["code_embed"]["dim"] = 768  # simulate model/dim swap
    (tmp_path / "meta.json").write_text(json.dumps(meta))

    conn = connect(settings); reset_db(conn); conn.close()
    assert snapshot.restore(str(tmp_path), settings) is False


def test_restore_returns_false_on_corrupt_dump(tmp_path, capsys):
    settings = _seed_and_dump(tmp_path)
    (tmp_path / "snapshot.dump").write_bytes(b"not a real pg_dump archive")

    conn = connect(settings); reset_db(conn); conn.close()
    assert snapshot.restore(str(tmp_path), settings) is False
    assert "pg_restore failed" in capsys.readouterr().err
