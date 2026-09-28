#!/usr/bin/env bash
# PostToolUse (git push, gh pr create): make sure every pushed branch gets a PR and every PR head gets a
# review. Prints a reminder for Claude; stays silent when nothing is missing or GitHub can't be reached.
set -uo pipefail
cd "${CLAUDE_PROJECT_DIR:-.}"
branch="$(git branch --show-current 2>/dev/null)"
case "$branch" in ""|main|master) exit 0 ;; esac
command -v gh >/dev/null || exit 0

remind() {
  python3 -c 'import json, sys; print(json.dumps({"hookSpecificOutput": {
      "hookEventName": "PostToolUse", "additionalContext": sys.argv[1]}}))' "$1"
  exit 0
}

pr="$(gh pr view "$branch" --json number,headRefOid,state -q 'select(.state == "OPEN") | "\(.number) \(.headRefOid)"' 2>/dev/null)"
if [ -z "$pr" ]; then
  remind "Branch '$branch' is pushed but has no open pull request: run the open-pr skill (it ends with review-pr)."
fi
read -r number sha <<<"$pr"
if gh api "repos/{owner}/{repo}/pulls/$number/reviews" --paginate -q '.[].body' 2>/dev/null \
    | grep -q "hone-review sha=$sha"; then
  exit 0
fi
remind "PR #$number has new commits (head $sha) without a review: run the review-pr skill for #$number, unless you are already doing so."
