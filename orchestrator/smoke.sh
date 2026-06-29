#!/usr/bin/env bash
# End-to-end smoke check for the single-task orchestrator. NOT part of the pytest
# unit suite. Requires Docker, a built stack, and CLAUDE_CODE_OAUTH_TOKEN in .env.
#
# Usage: PROJECT=/path/to/throwaway/git/repo bash orchestrator/smoke.sh
set -euo pipefail

PROJECT="${PROJECT:?set PROJECT to a throwaway git repo path}"
AGENT_IMAGE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# A trivial task the developer can complete in one shot.
mkdir -p "$PROJECT/tasks"
cat > "$PROJECT/tasks/smoke.md" <<'EOF'
# Add a greeting file

Create a file `HELLO.txt` at the repo root containing the single line `hello`.
EOF

cd "$PROJECT"
git add tasks/smoke.md && git commit -m "add smoke task" >/dev/null 2>&1 || true

echo "== running agentctl =="
PROJECT_DIR="$PROJECT" AGENT_IMAGE_DIR="$AGENT_IMAGE_DIR" \
  uv --directory "$AGENT_IMAGE_DIR/orchestrator" run agentctl run \
  --role developer tasks/smoke.md

echo "== assertions =="
branch=$(git -C "$PROJECT" branch --list 'feat/*' --sort=-committerdate --format='%(refname:short)' | head -1)
if [ -n "$branch" ] && git -C "$PROJECT" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null; then
  echo "PASS: task branch exists ($branch)"
else
  echo "FAIL: no task branch created"
fi
docker compose -f "$AGENT_IMAGE_DIR/docker-compose.yml" ps --status running --quiet \
  | grep -q . && echo "WARN: stack still up (check teardown)" || echo "PASS: stack torn down"
echo "smoke complete"
