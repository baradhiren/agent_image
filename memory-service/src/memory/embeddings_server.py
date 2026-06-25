"""Minimal HTTP embedding service (arm64-native fastembed).

Exposes the same surface `RemoteEmbeddingProvider` consumes:
    POST /embed  {"inputs": [str, ...]}  ->  [[float, ...], ...]
    GET  /health ->  {"status": "ok"}

This is the default embedding backend for the running stack, replacing TEI
(which has no linux/arm64 image). It wraps the in-process ``LocalEmbeddingProvider``
so the worker/agent images stay model-free and talk to it over HTTP.
"""

import os

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from memory.embeddings.local import LocalEmbeddingProvider

_provider = LocalEmbeddingProvider(os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5"))


async def embed(request: Request) -> JSONResponse:
    body = await request.json()
    inputs = body.get("inputs", [])
    if isinstance(inputs, str):
        inputs = [inputs]
    return JSONResponse(_provider.embed(inputs))


async def health(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "dim": _provider.dim})


app = Starlette(
    routes=[
        Route("/embed", embed, methods=["POST"]),
        Route("/health", health, methods=["GET"]),
    ]
)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "80")))


if __name__ == "__main__":
    main()
