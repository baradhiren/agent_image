import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from memory.config import Settings
from memory.db import apply_schema, connect
from memory.embeddings.base import EmbeddingProvider
from memory.embeddings.factory import build_embedder
from memory.repository import Repository


async def dispatch_tool(
    repo: Repository,
    code_embedder: EmbeddingProvider,
    doc_embedder: EmbeddingProvider,
    name: str,
    arguments: dict,
) -> list[TextContent]:
    if name == "search_code":
        payload = repo.search_code(code_embedder.embed([arguments["query"]])[0], arguments.get("k", 5))
    elif name == "search_docs":
        payload = repo.search_docs(doc_embedder.embed([arguments["query"]])[0], arguments.get("k", 5))
    elif name == "get_symbol":
        payload = repo.get_symbol(arguments["qualname"])
    elif name == "impact_of":
        payload = repo.impact_of(arguments["qualname"])
    elif name == "spec_for":
        payload = repo.spec_for(arguments["qualname"])
    elif name == "add_knowledge":
        repo.add_spec_link(arguments["spec_path"], arguments["symbol_qualname"])
        payload = {"status": "linked"}
    else:
        payload = {"error": f"unknown tool {name}"}
    return [TextContent(type="text", text=json.dumps(payload))]


def build_server(repo: Repository, code_embedder: EmbeddingProvider, doc_embedder: EmbeddingProvider) -> Server:
    server = Server("memory-service")

    def _obj(props: dict, required: list[str]) -> dict:
        return {"type": "object", "properties": props, "required": required}

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        q = {"query": {"type": "string"}, "k": {"type": "integer", "default": 5}}
        ql = {"qualname": {"type": "string"}}
        return [
            Tool(name="search_code", description="Semantic search over code chunks.", inputSchema=_obj(q, ["query"])),
            Tool(name="search_docs", description="Semantic search over doc/spec chunks.", inputSchema=_obj(q, ["query"])),
            Tool(name="get_symbol", description="Look up a symbol by qualified name.", inputSchema=_obj(ql, ["qualname"])),
            Tool(name="impact_of", description="List callers of a symbol (resolved edges).", inputSchema=_obj(ql, ["qualname"])),
            Tool(name="spec_for", description="List spec files linked to a symbol.", inputSchema=_obj(ql, ["qualname"])),
            Tool(name="add_knowledge", description="Link a spec file to a symbol.",
                 inputSchema=_obj({"spec_path": {"type": "string"}, "symbol_qualname": {"type": "string"}},
                                  ["spec_path", "symbol_qualname"])),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return await dispatch_tool(repo, code_embedder, doc_embedder, name, arguments)

    return server


def main() -> None:
    import asyncio

    settings = Settings.from_env()
    conn = connect(settings)
    apply_schema(conn, settings.code_embed.dim, settings.doc_embed.dim)
    repo = Repository(conn)
    repo.ensure_embedding_config("code", settings.code_embed.provider, settings.code_embed.model, settings.code_embed.dim)
    repo.ensure_embedding_config("doc", settings.doc_embed.provider, settings.doc_embed.model, settings.doc_embed.dim)
    server = build_server(repo, build_embedder(settings.code_embed), build_embedder(settings.doc_embed))

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())


if __name__ == "__main__":
    main()
