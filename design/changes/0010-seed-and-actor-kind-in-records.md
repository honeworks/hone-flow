# 0010: Seeds and who decided, in the records

## Status

`implemented in 0.1.0`, awaiting owner review (requested 2026-09-28 for
hone-lens changes 0003 and 0005, which read these attributes; additive, so it was built straight away)

## Context

hone-lens reads hone-flow's spans to explain runs. Two of its detectors need facts that the run folder
knows but the spans did not carry:

- **Which seed produced a sample.** The run seed is in `manifest.json` and each step's derived seed
  (`ctx.seed`) in its `metadata.json`, but not on the `hone.flow.run` / `hone.flow.step` spans, so a
  span store alone cannot tell two samples of a non-deterministic step apart by seed.
- **Whether a person or a program decided a gate.** Apps now let scripts and models review (for example
  a render check that rejects scenes that fail to render). `reviews[].actor` is a free name, so
  "a person approved it" and "a script approved it" look the same.

## Problem

Records should say which seed a step used and whether a gate decision was automated, without readers
guessing from names.

## Options

1. **Two attributes and one optional key**: `hone.flow.seed` on run spans (the run seed) and on step
   spans (the step's derived seed); `approve` / `edit` / `reject(..., automated=False)` recording
   `actor_kind` (`person` or `automated`) in the gate's `reviews` entry and as
   `hone.flow.gate.actor_kind` on the decision span.
2. Encode it in the actor name (`bot:render-check`): a convention every reader would have to know.

## Decision

Option 1. It is additive: `format_version` stays `"1"`; a review without `actor_kind` reads as
`person`.

## Consequences

- hone-lens can group samples by seed and separate automated from human decisions.
- CLI: `hone-flow approve|reject ... --automated`.

## Migration and compatibility

Additive: optional keyword `automated=False`, an optional `reviews[].actor_kind` key (default `person`),
two new span attributes. Older runs have neither; readers treat missing `actor_kind` as `person`.

## Implementation notes

- Step spans carry the seed the attempt used (`metadata.json` `seed`); reused steps carry the source's.
- AC-37 in `design/current.md` §7.
