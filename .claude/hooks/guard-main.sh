#!/usr/bin/env bash
# PreToolUse (Edit, Write): refuse to edit files on main, so every task happens on its own branch.
# Override for one session with HONE_ALLOW_MAIN=1.
set -euo pipefail
[ "${HONE_ALLOW_MAIN:-}" = "1" ] && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}"
branch="$(git branch --show-current 2>/dev/null || true)"
if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
  echo "You are on '$branch'. Start the task on its own branch first (the start-task skill)." >&2
  exit 2
fi
