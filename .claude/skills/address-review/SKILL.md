---
name: address-review
description: Act on the maintainer's answers to review comments on a pull request - fix, reply, resolve threads, push, re-review. Use when the maintainer says they answered the review or asks to handle the review comments.
argument-hint: "[pr-number]"
---

# Address a review

1. **Load the open threads** (`<n>` from `$ARGUMENTS` or `gh pr view --json number`):
   ```bash
   gh api graphql -F owner='{owner}' -F repo='{repo}' -F pr=<n> -f query='
     query($owner: String!, $repo: String!, $pr: Int!) {
       repository(owner: $owner, name: $repo) { pullRequest(number: $pr) {
         reviewThreads(first: 100) { nodes { id isResolved path line
           comments(first: 50) { nodes { databaseId author { login } body } } } } } } }'
   ```
   Also read the review bodies and PR comments: `gh pr view <n> --comments`. If the maintainer
   committed suggestions on GitHub, `git pull` first.
2. **Decide per unresolved thread**, from the maintainer's latest reply:
   | The maintainer... | Do |
   |---|---|
   | agrees, or says "fix it" | fix it (tests first if behaviour changes), reply "Fixed in `<sha>`" |
   | disagrees, or says "won't fix" | change nothing; reply briefly; if the reviewer was wrong, note it for `learn-from-reviews` |
   | asks a question | answer with evidence: file and line, a test, a command's output |
   | has not replied | leave it open and list it in the summary |
3. **Reply in the thread**:
   `gh api repos/{owner}/{repo}/pulls/<n>/comments/<databaseId>/replies -f body='...'`.
4. **Resolve** a thread only when it is fixed or the maintainer has decided it:
   `gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<id>"}) { thread { isResolved } } }'`.
5. If code changed: `sync-docs`, `verify-before-done`, commit (`fix: address review of #<n>`), `git push`.
   The push starts `review-pr` again, which reviews only the new commits.
6. Summarize: fixed, answered, still open.
