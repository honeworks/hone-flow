# 0005: Items from a step's output (dynamic fan-out)

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building explainer-channel (a demo app built on the honeworks packages). An episode is a concept turned into an
outline of N chapters, and then each chapter is narrated, storyboarded, illustrated and rendered. The
chapters are the natural items (per-chapter resume, per-chapter fork, breadth-first GPU batching: all
storyboards on the LLM, then all narration on the TTS model, then all pictures on ComfyUI), but N is only
known once the outline step has run, and a run's items are fixed when `wf.run(items)` starts.

The app therefore runs three workflows per episode and links them itself
(`explainer_channel/episode.py`): `script` (one item) produces the chapters, the app builds
`fk.Item`s from that output and starts a `chapters` run, then copies every chapter's video into a folder
and starts an `edit` run over one item whose input is that folder. It works, and fork still works per
chapter (fork the chapters run with one changed item input, then fork the edit run with the new folder),
but:

- one episode is three run folders plus an app-side state file naming them; hone-lens and `hone-flow
  show` see three unrelated runs;
- `resume` has to be orchestrated by the app (which stage is not done yet);
- the hand-over folder is copied into the edit run (about 250 MB of video per episode);
- the link from a chapter item back to the step that created it is only in the app's state file.

[0004](0004-steps-over-all-items.md) (fan-in, from course-builder) covers the second link (edit over all
chapters). This record covers the first: items created by a step.

## Problem

A step's list output cannot become the items of later steps in the same run.

## Options

1. **`@wf.step(fan_out="chapters")` / `fk.Items`**: a (global) step returns `fk.Items([fk.Item(...), ...])`;
   steps that declare `per="chapters"` run once per produced item, receiving the item's inputs like any
   item step. Produced items live in the manifest under their producer (`items_from: "<step>"`), their
   folders at `<step>/item_<id>/` as today. Fork compares produced items by id and inputs, so a changed
   chapter reruns only that chapter's downstream.
2. **Nested runs**: a step starts a child run (`ctx.child_run(wf2, items)`) recorded in the parent's
   manifest; resume and fork recurse. More general, heavier to read.
3. **Leave it to applications** (today; three runs and a state file).

## Decision

Proposed: option 1 together with 0004's `final_step`: global step -> produced items -> final step is the
common shape of five of the six showcase apps (explainer chapters, course lessons, series episodes,
lead lists, teacher lessons).

## Consequences

- Run format: produced items carry `items_from`; a run's item list grows while it runs (readers that list
  items from the manifest keep working).
- `until=` and `items_filter` apply to produced items once they exist; a resume after a crash in the
  producer reruns the producer before any produced item.
- `wf.validate()` checks that `per=` names a step that returns `fk.Items`.

## Implementation notes

- Option 1, without a separate `fan_out=` flag: a producer is a `@wf.global_step` whose return
  annotation is `fk.Items` (a list of `fk.Item`); consumers declare `@wf.step(per="<producer>")`
  (design §4.20). Each consumer declares `per=` explicitly; mixing item groups is a definition error.
- Produced items take JSON inputs only: a file in a produced item would point into the producer's
  temporary work folder. Files travel through step outputs instead.
- Their folders are `<step>/item_<id>/` as for the run's own items (not under the producer's folder), so
  every reader keeps working; the link to the producer is `items_from` in the manifest.
- Fork: when the producer reruns, the rows for its items are decided after it commits (same id and
  inputs: kept; changed: `produced_item_changed`; new: `new_item`; gone: dropped) and written to
  `fork_of.plan`; a dry run cannot know them.
- AC-32 in `design/current.md` §7; `examples/produced_items.py`; docs in `docs/across-items.md`.
