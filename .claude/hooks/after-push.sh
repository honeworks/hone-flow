#!/usr/bin/env bash
# PostToolUse (git push, gh pr create): make sure every pushed branch gets a pull request and every pull
# request head gets a review (the open-pr and review-pr skills). Prints a reminder for Claude. Stays
# silent when nothing is missing, and when GitHub can't be asked (offline, logged out, rate-limited):
# a failed call is never taken to mean "no pull request" or "not reviewed".
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

if ! pr="$(gh pr view "$branch" --json number,headRefOid,state \
    -q 'select(.state == "OPEN") | "\(.number) \(.headRefOid)"' 2>&1)"; then
  case "$pr" in
    *"no pull requests found"*) pr="" ;;   # gh answered: the branch has no pull request
    *) exit 0 ;;                           # gh failed: say nothing rather than guess
  esac
fi
if [ -z "$pr" ]; then
  remind "Branch '$branch' is pushed but has no open pull request: run the open-pr skill (it ends with review-pr)."
fi

read -r number sha <<<"$pr"
reviews="$(gh api "repos/{owner}/{repo}/pulls/$number/reviews" --paginate -q '.[].body' 2>/dev/null)" || exit 0
grep -qF "hone-review sha=$sha" <<<"$reviews" && exit 0
remind "PR #$number has new commits (head $sha) without a review: run the review-pr skill for #$number, unless you are already doing so."
