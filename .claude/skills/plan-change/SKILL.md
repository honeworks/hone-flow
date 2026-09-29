---
name: plan-change
description: Plan a design change as a change record in design/changes/ and get it approved before writing code. Use after start-task when a change touches the public API, CLI, guarantees, run format, records, ports, extras or dependencies.
---

# Plan a design change

1. Read `design/current.md` (§1 goals and non-goals, the §4 sections the change touches, §7 guarantees),
   `design/decisions.md` and the related records in `design/changes/`.
2. Next number: the highest `design/changes/NNNN-*.md` plus one.
3. Write `design/changes/NNNN-<short-name>.md` in the format of the existing records (for example
   `design/changes/0009-run-labels.md`): Status `proposed`, Context, Problem, Options (at least two,
   one of them "leave it as it is"), Decision, Consequences, Migration and compatibility. Include:
   - a sketch of the public API or CLI as a user would call it;
   - the new or changed acceptance cases, numbered after the last one in `design/current.md` §7;
   - the tests that will prove it (unit, e2e, crash or concurrency).
4. Answer each risk the change touches, in the record:
   - run folders stay self-contained; `metadata.json` is still written last; manifest writes stay
     conditional and under the run lease (`design/current.md` §4.4);
   - run format: optional keys keep `format_version`; anything else needs `deprecate-and-migrate`;
   - the core imports no extra; a new dependency is an optional extra with a reason in
     `design/decisions.md`;
   - secrets and webhook URLs are never recorded;
   - what happens on a crash between two writes, and with two processes on one run;
   - is there a smaller option that meets the need? (CONTRIBUTING.md, "Keep it simple")
5. Commit: `docs(design): propose NNNN <short name>`.
6. **Stop and ask.** Give the user the decision and the open questions in a few lines, with the file
   path. Only the project's maintainer accepts a record. If the user is the maintainer and approves, set
   the status to `accepted (approved by the maintainer YYYY-MM-DD)`, commit, and continue with
   `implement-change`. Otherwise the status stays `proposed`: offer to open a pull request with the record
   alone, and open it (`open-pr`) only when the user says so (AGENTS.md rule 10); or link the record from
   the issue. The maintainer decides there. On rejection set `rejected` with the reason and stop.
