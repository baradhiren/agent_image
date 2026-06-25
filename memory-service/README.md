# Memory Service (Phase 1)

Persistent knowledge layer: structure graph (resolved edges) + semantic embeddings
+ spec linkage, kept fresh by enqueue → worker → reconcile, exposed over MCP.
The embedding model runs as a **separate service** (default: an arm64-native
fastembed HTTP service, `memory.embeddings_server`; swap in TEI or a hosted
endpoint via `*_EMBED_URL`); our images are model-free.

## Run

```bash
docker compose up -d db          # Postgres + pgvector
uv sync --extra dev
uv run pytest -v                 # full suite (db running; uses local fastembed, no TEI needed)
docker compose up -d embeddings memory worker   # running stack (remote fastembed service)
```

## Choosing embedding models

Per collection, via env (independently for code and docs):
`CODE_EMBED_PROVIDER` (`local`|`remote`), `CODE_EMBED_URL`, `CODE_EMBED_MODEL`, `CODE_EMBED_DIM`
and the `DOC_EMBED_*` equivalents. Changing a model's dimension requires a reconcile/re-embed
(the `embedding_config` guard refuses silent mixing).

## Reconcile (drift safety net)

`uv run python -m memory.reconcile /path/to/project`

## MCP tools

`search_code`, `search_docs`, `get_symbol`, `impact_of`, `spec_for`, `add_knowledge`.
