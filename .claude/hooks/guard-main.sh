#!/usr/bin/env bash
# PreToolUse (Edit, Write, NotebookEdit): refuse to edit this repository's files on main, so every task
# happens on its own branch (the start-task skill).
#
# - Only files of this repository are guarded. Files anywhere else (Claude's memory and plan files,
#   scratch files, other repositories) and git-ignored files (.claude/settings.local.json) are allowed.
# - The branch is read where the edited file is, so a git worktree on a task branch works even while the
#   main checkout is on main.
# - Only the file-edit tools pass through this hook: shell edits and commits are not blocked.
# - Override for one session with HONE_ALLOW_MAIN=1.
set -uo pipefail
[ "${HONE_ALLOW_MAIN:-}" = "1" ] && exit 0

file="$(python3 -c 'import json, sys
i = json.load(sys.stdin).get("tool_input", {})
print(i.get("file_path") or i.get("notebook_path") or "")')"
[ -n "$file" ] || exit 0

# The nearest folder that exists: Write may create new folders.
dir="$(dirname "$file")"
while [ ! -d "$dir" ]; do dir="$(dirname "$dir")"; done

# Worktrees of one repository share its common git dir; anything else is another repository or none.
common() { git -C "$1" rev-parse --path-format=absolute --git-common-dir 2>/dev/null; }
here="$(common "$dir")" || exit 0
[ "$here" = "$(common "${CLAUDE_PROJECT_DIR:-.}")" ] || exit 0
git -C "$dir" check-ignore -q "$file" && exit 0

branch="$(git -C "$dir" branch --show-current)"
if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
  echo "You are on '$branch'. Start the task on its own branch first (the start-task skill)." >&2
  exit 2
fi
exit 0
