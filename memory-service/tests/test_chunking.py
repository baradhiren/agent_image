from memory.chunking import chunk_code, chunk_docs, normalize
from memory.models import ParsedFile, ParsedSymbol

SOURCE = "def foo():\n    return 1\n\nclass C:\n    def m(self):\n        return 2\n"


def test_chunk_code_skips_classes_and_breadcrumbs():
    parsed = ParsedFile(
        "m.py", "python", SOURCE,
        symbols=[
            ParsedSymbol("foo", "foo", "function", 1, 2),
            ParsedSymbol("C", "C", "class", 4, 6),
            ParsedSymbol("C.m", "m", "method", 5, 6),
        ],
        edges=[],
    )
    chunks = chunk_code(parsed)
    assert {c.chunk_key for c in chunks} == {"foo#0", "C.m#0"}
    foo = next(c for c in chunks if c.chunk_key == "foo#0")
    assert foo.text.startswith("# m.py > foo\n") and "return 1" in foo.text


def test_content_hash_ignores_trailing_whitespace():
    a = ParsedFile("m.py", "python", "def f():\n    return 1\n",
                   symbols=[ParsedSymbol("f", "f", "function", 1, 2)], edges=[])
    b = ParsedFile("m.py", "python", "def f():   \n    return 1  \n",
                   symbols=[ParsedSymbol("f", "f", "function", 1, 2)], edges=[])
    assert chunk_code(a)[0].content_hash == chunk_code(b)[0].content_hash


def test_oversized_function_splits():
    body = "def big():\n" + "\n".join(f"    a{i} = {i}" for i in range(50)) + "\n\n" + \
           "\n".join(f"    b{i} = {i}" for i in range(50)) + "\n"
    parsed = ParsedFile("m.py", "python", body,
                        symbols=[ParsedSymbol("big", "big", "function", 1, 103)], edges=[])
    assert {c.chunk_key for c in chunk_code(parsed, max_lines=80)} == {"big#0", "big#1"}


def test_chunk_docs():
    chunks = chunk_docs("r.md", "# T\n\nPara one.\n\nPara two.\n")
    assert [c.chunk_key for c in chunks] == ["r.md#0", "r.md#1", "r.md#2"]
    assert chunks[1].text == "Para one."
