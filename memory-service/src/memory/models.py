from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedSymbol:
    qualname: str
    name: str
    kind: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class ParsedEdge:
    src_qualname: str
    dst_name: str
    kind: str


@dataclass(frozen=True)
class ParsedFile:
    path: str
    language: str
    source: str
    symbols: list[ParsedSymbol]
    edges: list[ParsedEdge]
