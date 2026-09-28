# 0006: A revised producer receives its previous output

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building concept-shorts (a demo app built on the honeworks packages). Its `code` step writes manim code for every
scene of an episode; a `render_check` gate (reviewed by a script) rejects the step with a note naming
only the scenes that failed ("scene 4: NameError ...; scene 7: goes outside the safe area"). On
`resume()` the step reruns with `review_note`, but it does not get the output it produced last time, so
it cannot keep the scenes that passed and fix only the others. Rewriting every scene would throw away
work that passed and cost minutes of GPU time per revision.

The app works around it by reading its own run folder from inside the step
(`fk.open_runs(...).open_run(ctx.run_id).steps("code", item)` → `location` → the newest
`attempts/<n>/output/code.json`). That depends on the folder layout inside a step, not only on the
public run format, and every app that revises partially (lyrics with one bad verse, a shot list with two
bad shots) would repeat it.

## Problem

Reject-and-revise is most useful when the revision is incremental; the producer needs the rejected
value as well as the note.

## Options

1. **A `previous` parameter**, resolved like `review_note`: a producer that declares
   `previous: T | None = None` receives its latest rejected (or replaced) output, deserialized with the
   step's return type; `None` on the first attempt.
2. **`ctx.previous_output()`**, a method on the context doing the same lookup on demand.
3. **Leave it to apps** and document the read-API workaround.

## Decision

Proposed: option 1 (it keeps steps plain functions and shows the dependency in the signature), with
`ctx.previous_output()` as the building block it uses.

## Consequences

- Incremental revision becomes one parameter; the rejected value stays in `attempts/<n>/` as today.
- Fork: a fork reruns from scratch, so `previous` is `None` there unless the fork reuses attempts.

## Implementation notes

- `previous` is resolved by name like `review_note`; it is the latest attempt with status `rejected` or
  `replaced` that has outputs (a failed attempt's files are raw work files, not a typed output, so they
  are skipped). Several `outputs=` come as a tuple in declaration order.
- The value is loaded from the step's own `attempts/<n>/output/`, which is already in the run folder, so
  it is not snapshotted again into `inputs/`. `previous` is not a step input in the manifest's step
  table, so adding it does not make a run incompatible with resume.
- AC-33 in `design/current.md` §7; `examples/revise_incrementally.py`; docs in `docs/gates.md`.
