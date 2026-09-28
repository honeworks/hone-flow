#!/usr/bin/env bash
# PostToolUse (Edit, Write): format an edited Python file with ruff and fix what ruff can fix.
# What ruff cannot fix is shown to Claude (exit 2 after a tool only reports, it blocks nothing).
set -uo pipefail
file="$(python3 -c 'import json, sys; print(json.load(sys.stdin).get("tool_input", {}).get("file_path", ""))')"
case "$file" in *.py) ;; *) exit 0 ;; esac
[ -f "$file" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"
uv run --frozen ruff format --quiet "$file" >/dev/null 2>&1
if ! out="$(uv run --frozen ruff check --fix --quiet "$file" 2>&1)"; then
  echo "$out" >&2
  exit 2
fi
