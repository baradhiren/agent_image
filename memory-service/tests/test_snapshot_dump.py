# memory-service/tests/test_snapshot_dump.py
import json
import subprocess

import pytest

from memory.config import EmbedConfig, Settings
from memory.db import apply_schema, connect, reset_db
from memory import snapshot


def _seed_db():
    settings = Settings.from_env()
    conn = connect(settings)
    reset_db(conn)
    apply_schema(conn)
    conn.execute("INSERT INTO files (path, language, content_hash) VALUES ('a.py','python','h1')")
    conn.execute("INSERT INTO spec_links (spec_path, symbol_qualname) VALUES ('spec.md','a.fn')")
    conn.close()
    return settings


def test_dump_writes_all_three_files(tmp_path):
    settings = _seed_db()
    snapshot.dump(str(tmp_path), settings, source_head="deadbeef")

    assert (tmp_path / "snapshot.dump").exists()
    assert (tmp_path / "snapshot.dump").stat().st_size > 0
    assert (tmp_path / ".gitignore").read_text().strip() == "*"

    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["pg_major"] == 18
    assert meta["code_embed"]["dim"] == 384
    assert meta["source_head"] == "deadbeef"


def test_dump_does_not_clobber_existing_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("custom-content\n")
    settings = _seed_db()
    snapshot.dump(str(tmp_path), settings)
    assert (tmp_path / ".gitignore").read_text() == "custom-content\n"


def test_dump_raises_on_bad_database(tmp_path):
    settings = Settings(
        database_url="postgresql://postgres:postgres@localhost:5432/nope_no_db",
        code_embed=EmbedConfig("remote", "m", 384, None),
        doc_embed=EmbedConfig("remote", "m", 384, None),
    )
    with pytest.raises(Exception):
        snapshot.dump(str(tmp_path), settings)
    # atomic: no half-written snapshot left behind
    assert not (tmp_path / "snapshot.dump").exists()


def test_dump_cleans_up_temp_when_pg_dump_fails(tmp_path, monkeypatch):
    settings = _seed_db()

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "pg_dump")

    monkeypatch.setattr("memory.snapshot.subprocess.run", boom)

    with pytest.raises(subprocess.CalledProcessError):
        snapshot.dump(str(tmp_path), settings)

    # atomic: no snapshot file and no leftover temp files
    assert not (tmp_path / "snapshot.dump").exists()
    assert list(tmp_path.glob("*.tmp")) == []
