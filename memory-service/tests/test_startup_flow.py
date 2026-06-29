# memory-service/tests/test_startup_flow.py
from memory.config import Settings
from memory.db import apply_schema, connect, reset_db
from memory import snapshot, startup


def _project_with_one_file(tmp_path):
    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "mod.py").write_text("def hello():\n    return 1\n")
    return proj


def test_seed_path_when_no_snapshot(tmp_path):
    settings = Settings.from_env()
    conn = connect(settings); reset_db(conn); conn.close()
    proj = _project_with_one_file(tmp_path)

    result = startup.run_startup(str(proj), settings)

    assert result["restored"] is False
    assert result["location"] == "co-located"
    conn = connect(settings)
    files = conn.execute("SELECT count(*) FROM files").fetchone()[0]
    conn.close()
    assert files >= 1  # seeded from source


def test_restore_path_preserves_knowledge(tmp_path):
    settings = Settings.from_env()
    proj = _project_with_one_file(tmp_path)
    home = proj / ".agent-memory"
    home.mkdir()

    # build a DB with agent knowledge, then snapshot it into the project home.
    # Use the bare name 'hello': the Python parser names top-level functions by
    # bare name, so reconcile's prune_spec_links keeps this link (the referenced
    # symbol exists after reconcile) instead of deleting it as dangling.
    conn = connect(settings); reset_db(conn); apply_schema(conn)
    conn.execute("INSERT INTO spec_links (spec_path, symbol_qualname) VALUES ('s.md','hello')")
    conn.close()
    snapshot.dump(str(home), settings)

    # wipe live DB, then start up: should restore (not seed) and keep the link
    conn = connect(settings); reset_db(conn); conn.close()
    result = startup.run_startup(str(proj), settings)

    assert result["restored"] is True
    conn = connect(settings)
    links = conn.execute("SELECT count(*) FROM spec_links").fetchone()[0]
    conn.close()
    assert links == 1
