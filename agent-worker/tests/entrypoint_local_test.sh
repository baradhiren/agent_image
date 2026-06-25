#!/usr/bin/env bash
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
ENTRY="$REPO/agent-worker/entrypoint.sh"

WORK="$(mktemp -d)"
HOME_DIR="$WORK/home"
BIN="$WORK/bin"
MARKER="$WORK/bootstrap-ran"
mkdir -p "$HOME_DIR" "$BIN"

cat > "$BIN/claude" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$BIN/claude"

# Stub bootstrap.sh: touch a marker so we can assert whether it ran.
cat > "$BIN/bootstrap.sh" <<EOF
#!/usr/bin/env bash
touch "$MARKER"
EOF
chmod +x "$BIN/bootstrap.sh"

run() {  # run <role> <bootstrap-mode> <cmd...>
  local role="$1" mode="$2"; shift 2
  env -i PATH="$BIN:/usr/bin:/bin" HOME="$HOME_DIR" \
    CONFIG_DIR="$REPO/agent-worker/config" WORKSPACE="$WORK" \
    AGENT_ROLE="$role" BOOTSTRAP="$mode" bash "$ENTRY" "$@"
}

# Phase 2 still intact + common skill enabled; BOOTSTRAP=skip -> no bootstrap.
rm -rf "$HOME_DIR/.claude"; rm -f "$MARKER"
run developer skip true
grep -q "Engineering DNA" "$HOME_DIR/.claude/CLAUDE.md"
grep -q "Role: Developer" "$HOME_DIR/.claude/CLAUDE.md"
test -f "$HOME_DIR/.claude/skills/scaffold/SKILL.md"        # role skill
test -f "$HOME_DIR/.claude/skills/bootstrap/SKILL.md"       # common skill
test ! -f "$MARKER"
echo "PASS: phase-2 intact + common skill; BOOTSTRAP=skip does not run bootstrap"

# BOOTSTRAP=auto -> bootstrap runs.
rm -f "$MARKER"
run reviewer auto true
test -f "$MARKER"
echo "PASS: BOOTSTRAP=auto runs bootstrap"

rm -rf "$WORK"
echo "ALL PASS"
