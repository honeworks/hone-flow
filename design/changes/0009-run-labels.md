# 0009: Run labels: a human name and description for each run

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building run-browser (a demo app built on the honeworks packages). The owner, on its runs table: it "shows no useful,
human-readable information: no name, title or identifier that tells which run is which". A run is
identified only by its id (`20260928T074002Z-1838c3`, a time and random hex); its status, item count
and step counts look the same for every run of an app. What *does* tell runs apart is app knowledge:
the song's title (in the `idea` step's output), the course title (in `curriculum`), the series title, a
lead-finder run's date and number of posts.

run-browser now derives titles from per-workflow rules (TOML templates over step outputs, item inputs
and params) and heuristics. That works, but every tool (run-browser, hone-lens, `hone-flow runs`, a
Slack notification) would need the same rules, and the app that made the run knows its name best.
Notifications already carry a `summary`, but nothing names the run itself.

## Options

1. **A label set when the run starts, changeable later.**
   - `wf.run(items, ..., label: str | None = None, description: str | None = None)` and
     `run.fork(..., label=..., description=...)` (a fork defaults to `"<source label> (fork)"`).
   - `run.set_label(label, description=None)`: for apps that only know the name after a step (the
     song title comes from the `idea` step): a manifest write under the run lease, like reviews. A step
     could also call `ctx.set_run_label(...)`, applied when its result is committed.
   - Stored as optional manifest keys `label`, `description` (`format_version` stays `"1"`); in
     `RunSummary` (`label`, `description`), in the `hone.flow.run` span attributes
     (`hone.flow.run.label`), in notification payloads, and in the CLI (`hone-flow run --label`,
     `hone-flow runs` shows it; `hone-flow label RUN_ID TEXT`).
   - Secret stripping applies as for params.
2. **Only at start** (`wf.run(label=...)`): simplest, but the most useful names come from a step's output.
3. **Leave naming to readers' rules** (today): every reader duplicates app knowledge.

## Decision

Proposed: option 1. Labels are plain optional data: no behaviour depends on them, readers that do not
know the keys ignore them, and old runs simply have none (readers fall back to their own rules).

## Consequences

- Run format: optional `label`, `description` in `manifest.json` (and `RunSummary`); documented in
  `run-format.md` and `read-api.md`.
- The demo apps set labels (oneshot-studio: the idea's title; course-builder: the course title;
  concept-shorts: the series title; lead-finder: `Leads <date>`), and run-browser's rules stay as the
  fallback for runs made before.
- `set_label` takes the run lease, so it waits for (or fails fast against) a process running the run,
  like `approve`.

## Implementation notes

- `run.set_label(label, description=None)`: `description=None` keeps the current description; `None` or
  blank text removes a value. A pinned archive refuses it (archives never change).
- Notification messages carry `label` only when the run has one, so existing receivers see the same
  payload for unlabelled runs; Slack / Mattermost / Discord text shows it as the second line.
- AC-36 in `design/current.md` §7; `examples/run_labels.py`.
