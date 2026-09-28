---
name: review-pr
description: Review a pull request with fresh reviewer agents that have no conversation context, check their findings, and post the kept ones to GitHub as a review with inline comments and suggested changes. Runs after open-pr and after every push to a PR branch, without being asked.
argument-hint: "[pr-number]"
---

# Review a pull request

1. **The PR.** `$ARGUMENTS`, or `gh pr view --json number,headRefOid,url`. The head sha is `headRefOid`.
   Earlier reviews end with `<!-- hone-review sha=<sha> -->`
   (`gh api repos/{owner}/{repo}/pulls/<n>/reviews`); take the newest marker.
   - It equals the head: already reviewed, stop.
   - It is an ancestor of the head (`git merge-base --is-ancestor <sha> <head>`) and
     `git log --merges <sha>..<head>` is empty: review only `<sha>..<head>`.
   - Otherwise (no marker, a rebase, or `main` merged into the branch): review `origin/main...<head>`.
2. **Fresh reviewers.** Start these subagents in parallel. Give each only the PR number, the commit range
   and "review it"; never a summary of the work or your opinion of it:
   `pr-reviewer`, `test-auditor`, `simplicity-reviewer`. If the `pr-review-toolkit` plugin is installed,
   add `silent-failure-hunter` and `type-design-analyzer` when `src/` changed.
3. **Triage** every finding by reading the code at that line yourself:
   - keep: real, on a line this PR changed, worth fixing;
   - drop: false positive, older than this PR, a nitpick, or something ruff, pyright or the tests catch.
     Note a one-line reason.
   Merge duplicates. Severity: **blocking** (bug, broken rule or guarantee, missing test for new
   behaviour, missing doc update), **should fix**, **nit**.
4. **Verdict.** `REQUEST_CHANGES` if anything blocking is kept; `APPROVE` if nothing is kept; otherwise
   `COMMENT`.
5. **The review**, as JSON in a temporary file:
   ```json
   {"commit_id": "<head sha>", "event": "<verdict>", "body": "<summary>",
    "comments": [{"path": "src/hone_flow/x.py", "line": 42, "side": "RIGHT", "body": "..."}]}
   ```
   - One inline comment per kept finding: `**blocking** · AGENTS.md rule 4`, the problem, the fix. Use
     `start_line` and `line` for a range. When the fix is exact, add a GitHub suggestion block
     (a fenced block with the language `suggestion` holding the replacement lines).
   - The body: the verdict on the first line, counts, kept findings that fit no diff line, the dropped
     findings with reasons inside `<details>`, and `<!-- hone-review sha=<head sha> -->` last.
6. **Post**: `gh api repos/{owner}/{repo}/pulls/<n>/reviews --method POST --input <file>`.
   - With `HONEWORKS_REVIEWER_TOKEN` set (the reviewer account), prefix `GH_TOKEN="$HONEWORKS_REVIEWER_TOKEN"`.
     It may send `REQUEST_CHANGES` or `COMMENT`, never `APPROVE`: approving is a person's decision, and a
     bot approval could count toward branch protection. With nothing kept, send `COMMENT` and write
     "Approve" as the verdict in the body.
   - Without it the review comes from the PR author, and GitHub allows only `COMMENT` on your own PR:
     send `"event": "COMMENT"` and keep the verdict in the body's first line.
   - A 422 for a line outside the diff: move that comment into the body and post again.
7. Tell the user: the review link, the verdict, one line per kept finding. The answers come on GitHub;
   `address-review` handles them.
