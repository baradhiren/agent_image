#!/usr/bin/env bash
# Deterministic, idempotent, re-runnable toolset bootstrap.
# Runtimes via mise (.mise.toml/.tool-versions); setup via specs/toolset.yaml.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
TOOLSET_YAML="$WORKSPACE/specs/toolset.yaml"

# Print each top-level `setup:` list item (one shell command per line).
# Minimal, well-defined subset: a top-level `setup:` key followed by `- ` items.
parse_setup() {
  local in_setup=0 line item
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    if [[ $line == setup:* ]]; then
      in_setup=1
      continue
    fi
    [ "$in_setup" -eq 1 ] || continue
    # A new top-level key (no leading space, not a comment or list item) ends it.
    if [[ $line =~ ^[^[:space:]#-].*: ]]; then
      in_setup=0
      continue
    fi
    if [[ $line =~ ^[[:space:]]*-[[:space:]]+(.*)$ ]]; then
      item="${BASH_REMATCH[1]}"
      item="${item%"${item##*[![:space:]]}"}"            # rstrip trailing space
      if [[ $item =~ ^\"(.*)\"$ ]] || [[ $item =~ ^\'(.*)\'$ ]]; then
        item="${BASH_REMATCH[1]}"                          # strip wrapping quotes
      fi
      [ -n "$item" ] && printf '%s\n' "$item"
    fi
  done < "$1"
}

cd "$WORKSPACE" 2>/dev/null || {
  echo "bootstrap: workspace $WORKSPACE not found; nothing to do"
  exit 0
}

ran_install=0
ran_setup=0

# 1. Runtimes via mise, if the project declares them.
if [ -f "$WORKSPACE/.mise.toml" ] || [ -f "$WORKSPACE/.tool-versions" ]; then
  mise install
  mise reshim
  ran_install=1
fi

# 2. Setup commands from specs/toolset.yaml, in order, under the mise env.
if [ -f "$TOOLSET_YAML" ]; then
  while IFS= read -r cmd; do
    [ -n "$cmd" ] || continue
    echo "bootstrap: setup: $cmd"
    mise exec -- bash -c "$cmd"
    ran_setup=$((ran_setup + 1))
  done < <(parse_setup "$TOOLSET_YAML")
fi

# 3. Summary.
if [ "$ran_install" -eq 0 ] && [ "$ran_setup" -eq 0 ]; then
  echo "bootstrap: no toolset config found; nothing to do"
else
  echo "bootstrap: done (runtimes installed: $ran_install, setup commands run: $ran_setup)"
fi
