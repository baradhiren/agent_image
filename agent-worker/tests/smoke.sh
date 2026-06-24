#!/usr/bin/env bash
# Phase 2 smoke checks. Run from anywhere; operates from the repo root.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "== 1. build agent-worker =="
docker compose build agent-worker

echo "== 2. role compose + invalid-role fail-fast =="
docker compose run --rm --no-deps -e AGENT_ROLE=reviewer agent-worker \
  bash -c 'grep -q "Engineering DNA" ~/.claude/CLAUDE.md \
        && grep -q "Role: Reviewer" ~/.claude/CLAUDE.md \
        && test -f ~/.claude/skills/code-check/SKILL.md'
echo "  reviewer overlay + skill ok"
if docker compose run --rm --no-deps -e AGENT_ROLE=bogus agent-worker true 2>/dev/null; then
  echo "  FAIL: invalid role did not fail fast"; exit 1
fi
echo "  invalid role fails fast ok"

echo "== 3. claude --version =="
docker compose run --rm --no-deps agent-worker claude --version

echo "== 4. claude mcp list shows memory =="
# Buffer the output first: piping straight into `grep -q` closes the pipe on the
# first match and SIGPIPEs claude, which `pipefail` would treat as a failure.
mcp_out="$(docker compose run --rm --no-deps agent-worker claude mcp list)"
echo "$mcp_out" | grep -q memory
echo "  memory server registered ok"

echo "== 5. in-container memory connectivity (shared db) =="
# Proves the agent-worker container reaches the shared Postgres and runs the
# memory package's search path. Uses the in-process `local` fastembed provider
# (the design's offline/test default) so the check stays hermetic and does not
# depend on the TEI embeddings server -- whose cpu-1.5 image has no linux/arm64
# manifest and so cannot run on this Apple Silicon host.
docker compose up -d --wait db
docker compose run --rm --no-deps \
  -e CODE_EMBED_PROVIDER=local -e DOC_EMBED_PROVIDER=local \
  agent-worker /opt/memory/.venv/bin/python - <<'PY'
from memory.config import Settings
from memory.db import connect, apply_schema
from memory.repository import Repository
from memory.embeddings.factory import build_embedder

s = Settings.from_env()
conn = connect(s)
apply_schema(conn, s.code_embed.dim, s.doc_embed.dim)
repo = Repository(conn)
repo.ensure_embedding_config("code", s.code_embed.provider, s.code_embed.model, s.code_embed.dim)
repo.ensure_embedding_config("doc", s.doc_embed.provider, s.doc_embed.model, s.doc_embed.dim)
emb = build_embedder(s.code_embed)
results = repo.search_code(emb.embed(["hello world"])[0], 5)
print("search_code ok, rows:", len(results))
PY

echo "== cleanup =="
docker compose down

echo "ALL SMOKE CHECKS PASSED"
