import hashlib
from dataclasses import dataclass

from memory.models import ParsedFile


@dataclass(frozen=True)
class CodeChunk:
    chunk_key: str
    qualname: str
    content_hash: str
    text: str


@dataclass(frozen=True)
class DocChunk:
    chunk_key: str
    path: str
    content_hash: str
    text: str


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def _hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf8")).hexdigest()


def _split_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip() == "" and current:
            blocks.append(current)
            current = []
        elif line.strip():
            current.append(line)
    if current:
        blocks.append(current)
    return blocks or [lines]


def chunk_code(parsed: ParsedFile, max_lines: int = 80) -> list[CodeChunk]:
    src_lines = parsed.source.splitlines()
    out: list[CodeChunk] = []
    for s in parsed.symbols:
        if s.kind not in ("function", "method"):
            continue
        body_lines = src_lines[s.start_line - 1 : s.end_line]
        breadcrumb = f"# {parsed.path} > {s.qualname}"
        parts = [body_lines] if len(body_lines) <= max_lines else _split_blocks(body_lines)
        for i, part in enumerate(parts):
            body = "\n".join(part)
            out.append(CodeChunk(f"{s.qualname}#{i}", s.qualname, _hash(body), f"{breadcrumb}\n{body}"))
    return out


def chunk_docs(path: str, text: str) -> list[DocChunk]:
    out: list[DocChunk] = []
    i = 0
    for block in (b.strip() for b in text.split("\n\n")):
        if not block:
            continue
        out.append(DocChunk(f"{path}#{i}", path, _hash(block), block))
        i += 1
    return out
