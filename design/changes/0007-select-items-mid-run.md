# 0007: Choose which items continue, mid-run (a selection across items)

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building lead-finder (a demo app built on the honeworks packages). One run per day, one item per job post (45 posts):
`fetch -> parse -> company -> fit` for every post, then a **selection across items** (one lead per
company, embedding dedup, the top 10 by fit), then `draft -> review` only for the 10 kept leads.

hone-flow has no step that sees all items and decides which of them go on. The app does it between two
calls on the same run: `wf.run(items, until="fit")`, the shortlist in plain Python (saved to the app's
own `data/shortlists/<run_id>.json`), then `run.resume(items=kept)`. It works and keeps one run folder
per day, but:

- the shortlist (the most important decision of the day) is outside the run folder: no snapshot, no
  span, invisible to hone-lens and `hone-flow show`;
- the run ends `partial` for ever: the 35 leads not kept are `skipped`, which reads like unfinished work,
  and a bare `run.resume()` (or `hone-flow resume`) would draft all 45;
- the app must make `finish()` re-entrant by hand (was the shortlist made? which items were kept?).

[0004](0004-steps-over-all-items.md) (`final_step`, fan-in) would let a step *read* every item's output,
but a final step runs after all items; here the fan-in sits in the middle and decides the rest.

## Options

1. **`@wf.select_step()`**: like 0004's final step (a parameter named after an item step receives
   `dict[item_id, output]`), but it returns the item ids that continue (plus optional reasons for the
   others). Item steps downstream of it run only for those ids; the others get state `not_selected`
   (a terminal state that counts as done for the run status), with the reason in their metadata. Its
   output is stored like a global step's.
2. **A state for "dropped by the app"**: `run.drop(items, reason)` marks the remaining steps of those
   items `not_selected`; the selection stays in app code but the run can complete and records why.
   Smaller, but the decision is still outside the run's records.
3. Leave it to applications (today).

## Decision

Proposed: option 1, with option 2's `not_selected` state. Shortlists, top-N after scoring, "only the
lessons that failed review", "only the episodes whose render failed" are the same shape. Fork semantics
follow 0004: refreshing an item step before the select step reruns the select step, and items whose
selection changed run or become `not_selected`.

## Consequences

- Run format: a new step kind `select` and a new terminal state `not_selected`; `completed` means every
  step is `done` or `not_selected`.
- `resume(items=...)` of a `not_selected` item is refused unless forced.

## Implementation notes

- `@wf.select_step(partial_ok=False)`, kind `select`; it returns a list of ids or `fk.Selection(keep,
  reasons)` and is stored as `fk.Selection` (design §4.19). The steps that follow the choice name the
  select step as a parameter (they receive the `fk.Selection`), so the DAG stays inferred from names.
- Simplified: the reasons are kept in the select step's output only, not in a `metadata.json` per
  not-selected unit (`not_selected` units have no folder, like `skipped` and `blocked` ones).
- Simplified: `resume(items=[...])` of a `not_selected` item is refused without a `force=` escape; a fork
  with those items (or a refreshed select step) runs them.
- `partial_ok=True` (as for final steps) lets the selection go on when some items failed; lead-finder's
  unreadable posts need it. A failed item's downstream stays `blocked`, not `not_selected`.
- Fork: when the select step reruns, every kept item's steps that take its output rerun (their input, the
  selection, changed); reusing the drafts of items whose membership did not change would need the fork
  plan to know the new selection before it is made. An unchanged selection is reused with its
  `not_selected` units.
- AC-34 in `design/current.md` §7; `examples/select_items.py`; docs in `docs/across-items.md`.
