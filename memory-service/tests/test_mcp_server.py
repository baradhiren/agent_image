import json

import pytest

from memory.chunking import CodeChunk
from memory.embeddings.local import LocalEmbeddingProvider
from memory.mcp_server import build_server, dispatch_tool
from memory.models import ParsedFile, ParsedSymbol
from memory.repository import Repository

EMB = LocalEmbeddingProvider()


@pytest.fixture()
def repo(conn):
    r = Repository(conn)
    fid = r.upsert_file_row("svc.py", "python", "h")
    r.replace_structure(fid, ParsedFile("svc.py", "python", "x",
                        [ParsedSymbol("helper", "helper", "function", 1, 2)], []))
    r.sync_code_chunks(fid, [CodeChunk("helper#0", "helper", "h1", "database connection helper")], EMB)
    return r


def test_build_server_registers(repo):
    # build_server must register tool/list handlers without raising.
    assert build_server(repo, EMB, EMB) is not None


@pytest.mark.anyio
async def test_search_code_tool(repo):
    result = await dispatch_tool(repo, EMB, EMB, "search_code", {"query": "database helper", "k": 1})
    assert json.loads(result[0].text)[0]["qualname"] == "helper"


@pytest.mark.anyio
async def test_add_knowledge_then_spec_for(repo):
    await dispatch_tool(repo, EMB, EMB, "add_knowledge", {"spec_path": "specs/x.md", "symbol_qualname": "helper"})
    result = await dispatch_tool(repo, EMB, EMB, "spec_for", {"qualname": "helper"})
    assert json.loads(result[0].text) == ["specs/x.md"]
