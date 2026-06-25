from pathlib import PurePosixPath

from memory.parser.base import LanguageParser
from memory.parser.js_ts_parser import JsTsParser
from memory.parser.python_parser import PythonParser

_PYTHON = ("python", PythonParser())
_TS = ("javascript", JsTsParser("typescript"))
_TSX = ("javascript", JsTsParser("tsx"))
_JS = ("javascript", JsTsParser("javascript"))

_REGISTRY: dict[str, tuple[str, LanguageParser]] = {
    ".py": _PYTHON,
    ".ts": _TS,
    ".tsx": _TSX,
    ".js": _JS,
    ".jsx": _JS,
    ".mjs": _JS,
    ".cjs": _JS,
}


def code_parser_for(rel_path: str) -> tuple[str, LanguageParser] | None:
    return _REGISTRY.get(PurePosixPath(rel_path).suffix)


def code_extensions() -> frozenset[str]:
    return frozenset(_REGISTRY)
