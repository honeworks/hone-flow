---
name: learn-from-reviews
description: Turn repeated review feedback into rules - find problems that reviews keep catching, and reviewer comments the maintainer keeps rejecting, and propose updates to AGENTS.md, CONTRIBUTING.md, the reviewer agents or the skills. Use when the maintainer asks what reviews keep finding, or every few merged PRs.
---

# Learn from past reviews

1. Collect the last merged PRs (`gh pr list --state merged --limit 30 --json number,title`) and, for
   each, its reviews and threads with the maintainer's replies (the query in `address-review`).
2. Group the findings by rule or theme and look for:
   - the same problem in two or more PRs: a rule to add or sharpen (`AGENTS.md`, `CONTRIBUTING.md`) or,
     better, a check to automate (a test, a ruff rule, a hook);
   - reviewer comments the maintainer rejected two or more times: a false positive; fix the checklist of
     that agent in `.claude/agents/`;
   - problems found only after a merge: a missing checklist item.
3. Report: theme, PR links, the change you propose.
4. With the maintainer's OK, make the changes as a normal task (`start-task`, type `chore`).
