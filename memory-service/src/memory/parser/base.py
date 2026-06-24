from typing import Protocol

from memory.models import ParsedFile


class LanguageParser(Protocol):
    def parse(self, path: str, source: str) -> ParsedFile: ...
