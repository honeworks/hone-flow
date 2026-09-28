@AGENTS.md

## Claude Code

Every task follows one flow. Each step is a skill in `.claude/skills/`:

```text
new task      -> start-task (branch) -> design change? plan-change, then wait for approval
              -> implement-change (tests first) -> sync-docs -> verify-before-done
"push" / "ship it" / "I'm happy"
              -> open-pr -> review-pr (fresh reviewer agents; review posted on GitHub)
answers on GitHub -> address-review -> push -> review-pr again
merged        -> finish-task
```

- Never edit on `main`: `.claude/hooks/guard-main.sh` blocks it (`HONE_ALLOW_MAIN=1` overrides it).
- Every push to a PR branch is followed by `review-pr`; `.claude/hooks/after-push.sh` reminds you.
- More skills: `debug-failure`, `add-example`, `add-adapter`, `deprecate-and-migrate`,
  `real-model-tests`, `triage-issue`, `learn-from-reviews`, and `release` (only when the maintainer asks).
- Reviewer agents in `.claude/agents/`: `pr-reviewer`, `test-auditor`, `simplicity-reviewer`.
- Recommended plugins, enabled in `.claude/settings.json`; install them once with
  `/plugin install pyright-lsp@claude-plugins-official` and
  `/plugin install pr-review-toolkit@claude-plugins-official`.
- Settings for your machine (model names, `HONE_TEST_*`, extra permissions, `HONEWORKS_REVIEWER_TOKEN`)
  go in `.claude/settings.local.json`, which is not committed.
