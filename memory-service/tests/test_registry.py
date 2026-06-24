from memory.parser.js_ts_parser import JsTsParser
from memory.parser.python_parser import PythonParser
from memory.parser.registry import code_parser_for


def test_python_extension():
    lang, parser = code_parser_for("a/b/svc.py")
    assert lang == "python"
    assert isinstance(parser, PythonParser)


def test_ts_js_extensions_are_javascript_family():
    for path in ("x.ts", "x.tsx", "x.js", "x.jsx", "x.mjs", "x.cjs"):
        entry = code_parser_for(path)
        assert entry is not None, path
        lang, parser = entry
        assert lang == "javascript"
        assert isinstance(parser, JsTsParser)


def test_unsupported_and_markdown_return_none():
    assert code_parser_for("README.md") is None
    assert code_parser_for("data.json") is None
    assert code_parser_for("noext") is None
