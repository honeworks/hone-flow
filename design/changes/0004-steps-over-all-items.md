# 0004: Steps over all items (fan-in)

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building course-builder (a demo app built on the honeworks packages). A course is one run: the curriculum is a
global step and each lesson is an item (script, quiz, summary, slides, narration, video). The course
also needs two products made from **all** lessons: the workbook PDF (every lesson's summary, exercise
and quiz, then one answer key) and the course page (`course.md`, the lesson list with durations).

hone-flow has fan-out (global step -> items) but no fan-in. A global step may not depend on item steps,
and an item step sees only its own item. So the app builds the workbook and the course page in the CLI
after `wf.run` returns (`outputs.export`). That works, but those two products are outside the run
folder: no inputs/outputs snapshot, no timing, not rerun by `fork` when one lesson is refreshed (the app
has to remember to re-export), not visible to hone-lens.

The other showcase apps have the same shape (an explainer from its scenes, a channel page from its
episodes, a shortlist from its leads).

## Problem

- No step can take the outputs of one step for every item.
- Work that combines items falls outside the run's records, resume and fork.

## Options

1. **`@wf.final_step()`**: runs once after all items; a parameter named after an item step receives
   `dict[item_id, output]` (only `done` items; the step is `blocked` if any item it needs is not done,
   unless `partial_ok=True`). Stored like a global step at `<step>/`, inputs snapshotted per item.
2. **A second workflow over one item** whose input is the first run's id; it reads outputs through the
   read API. Works today, but two run folders per course, and fork of the first does not reach the second.
3. Leave it to applications (today).

## Decision

Proposed: option 1. It mirrors `global_step` (same folder layout and records), keeps the DAG inferred
from parameter names, and fork's downstream rule extends naturally: refreshing one item's step reruns
that item's downstream and every final step that consumes it.

## Consequences

- Run format: a new step kind `final` in the manifest; readers that ignore unknown kinds keep working.
- `resume(items=[...])` / `until=` must define what a final step does for a partial selection (proposed:
  `skipped` unless every needed item is done).

## Also found: refreshing one step for one item

`run.fork(refresh=("script",), items=["02"])` keeps only item 02 in the fork, and without `items` the
refresh applies to every item. course-builder refreshes one lesson by forking with a changed item input
(the lesson's revision `note`), which is a good fit there, but a plain "rerun this step for this item,
copy the rest" has no direct form. A small addition: `refresh={"script": ["02"]}` (a mapping from step to
item ids) next to the existing tuple form.

## Implementation notes

- Option 1 as proposed: `@wf.final_step(partial_ok=False)`, kind `final`, per-item snapshots under
  `inputs/<parameter>/<item id>/`, input `from: "items:<step>"` (design §4.18).
- Partial selections: a final step whose needed items were left out by the call is `skipped` (so a later
  `resume()` runs it); an item waiting at a gate leaves it `pending`; a failed item `blocked`.
- Depth-first order walks phases split at fan-in steps (`schedule.py`).
- Fork: a final step is downstream of every item; a fork with other item ids gives it the new reason
  `items_changed`. The "also found" part is implemented as `refresh={"script": ["02"]}` (CLI
  `--refresh script=02`).
- AC-31 in `design/current.md` §7; `examples/fan_in.py`; docs in `docs/across-items.md`.
