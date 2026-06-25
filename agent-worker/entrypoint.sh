#!/usr/bin/env bash
set -euo pipefail

CONFIG_DIR="${CONFIG_DIR:-/opt/agent-worker/config}"
WORKSPACE="${WORKSPACE:-/workspace}"
MEMORY_PYTHON="${MEMORY_PYTHON:-/opt/memory/.venv/bin/python}"
AGENT_ROLE="${AGENT_ROLE:-developer}"
HOME_DIR="${HOME:-/home/agent}"
BOOTSTRAP="${BOOTSTRAP:-auto}"
export WORKSPACE

# 1. Validate the role.
case "$AGENT_ROLE" in
  developer|reviewer|design) ;;
  *)
    echo "entrypoint: invalid AGENT_ROLE '$AGENT_ROLE' (expected developer|reviewer|design)" >&2
    exit 1
    ;;
esac

mkdir -p "$HOME_DIR/.claude/skills"

# 2. Compose instructions: base engineering DNA + role overlay -> global CLAUDE.md
#    (global, so the mounted project is never written to).
cat "$CONFIG_DIR/AGENTS.md" "$CONFIG_DIR/roles/$AGENT_ROLE.md" > "$HOME_DIR/.claude/CLAUDE.md"

# 3. Enable skills: the role's scoped skills + cross-role common skills.
if [ -d "$CONFIG_DIR/skills/$AGENT_ROLE" ]; then
  cp -r "$CONFIG_DIR/skills/$AGENT_ROLE/." "$HOME_DIR/.claude/skills/"
fi
if [ -d "$CONFIG_DIR/skills/common" ]; then
  cp -r "$CONFIG_DIR/skills/common/." "$HOME_DIR/.claude/skills/"
fi

# 4. Register the memory MCP server (stdio subprocess; inherits DB/embeddings env).
if command -v claude >/dev/null 2>&1; then
  claude mcp remove memory --scope user >/dev/null 2>&1 || true
  claude mcp add memory --scope user -- "$MEMORY_PYTHON" -m memory.mcp_server
fi

# 5. Bootstrap the project's toolset (warn and continue on failure).
cd "$WORKSPACE" 2>/dev/null || true
if [ "$BOOTSTRAP" != "skip" ] && command -v bootstrap.sh >/dev/null 2>&1; then
  if ! bootstrap.sh; then
    echo "entrypoint: bootstrap failed; continuing (re-run the bootstrap skill once fixed)" >&2
  fi
fi

# 6. Run the requested command (default: bash).
exec "$@"
