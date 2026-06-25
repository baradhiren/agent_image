#!/usr/bin/env bash
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
BOOT="$REPO/agent-worker/bootstrap.sh"

WORK="$(mktemp -d)"
BIN="$WORK/bin"
export MISE_LOG="$WORK/mise.log"
mkdir -p "$BIN"
: > "$MISE_LOG"

# Stub mise: log install/reshim/etc; pass `exec -- <cmd>` through so setup runs.
cat > "$BIN/mise" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  exec)
    shift
    [ "${1:-}" = "--" ] && shift
    exec "$@"
    ;;
  *) echo "$@" >> "$MISE_LOG" ;;
esac
EOF
chmod +x "$BIN/mise"

run() {  # run <workspace-dir>
  env PATH="$BIN:/usr/bin:/bin" MISE_LOG="$MISE_LOG" WORKSPACE="$1" bash "$BOOT"
}

# (a) mise config present -> install + reshim
P="$WORK/a"; mkdir -p "$P"; echo "node 18.20.4" > "$P/.tool-versions"
: > "$MISE_LOG"
run "$P" >/dev/null
grep -q '^install' "$MISE_LOG"
grep -q '^reshim' "$MISE_LOG"
echo "PASS: mise install + reshim on .tool-versions"

# (b) setup commands run in order
P="$WORK/b"; mkdir -p "$P/specs"
cat > "$P/specs/toolset.yaml" <<'YML'
setup:
  - echo a >> order.txt
  - echo b >> order.txt
YML
run "$P" >/dev/null
[ "$(tr -d '[:space:]' < "$P/order.txt")" = "ab" ]
echo "PASS: setup commands run in order"

# (c) abort on first failing setup command
P="$WORK/c"; mkdir -p "$P/specs"
cat > "$P/specs/toolset.yaml" <<'YML'
setup:
  - echo one >> ran.txt
  - false
  - echo three >> ran.txt
YML
if run "$P" >/dev/null 2>&1; then echo "FAIL: did not abort"; exit 1; fi
grep -q one "$P/ran.txt"
if grep -q three "$P/ran.txt" 2>/dev/null; then echo "FAIL: ran past failure"; exit 1; fi
echo "PASS: aborts on failing setup command"

# (d) no config -> no-op success
P="$WORK/d"; mkdir -p "$P"
out="$(run "$P")"
echo "$out" | grep -q "nothing to do"
echo "PASS: no-op when no config"

rm -rf "$WORK"
echo "ALL PASS"
