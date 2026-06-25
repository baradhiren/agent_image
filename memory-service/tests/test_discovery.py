from pathlib import Path

from memory.discovery import (
    SUPPORTED_EXTENSIONS,
    is_supported,
    iter_source_files,
)


def test_supported_extensions_cover_code_and_docs():
    assert {".py", ".ts", ".tsx", ".js", ".jsx", ".md"} <= SUPPORTED_EXTENSIONS


def test_is_supported():
    assert is_supported("a/b/app.ts")
    assert is_supported("README.md")
    assert not is_supported("notes.txt")
    assert not is_supported("Makefile")


def test_iter_source_files_prunes_junk_dirs(tmp_path: Path):
    (tmp_path / "app.ts").write_text("const x = 1\n")
    (tmp_path / "readme.md").write_text("# r\n")
    (tmp_path / "main.py").write_text("y = 1\n")
    (tmp_path / "data.txt").write_text("ignore\n")
    for junk in (".venv/lib", "node_modules/pkg", ".git", "__pycache__", "dist"):
        d = tmp_path / junk
        d.mkdir(parents=True)
        (d / "dep.py").write_text("z = 2\n")
        (d / "dep.js").write_text("z = 2\n")
    assert iter_source_files(str(tmp_path)) == ["app.ts", "main.py", "readme.md"]
