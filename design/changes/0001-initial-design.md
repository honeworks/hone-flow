# 0001: Initial design: content-addressed assets, SQLite metadata and a cross-run cache

## Status

`superseded by 0002` ([run folders](0002-run-folders.md)). Built as the first 0.1.0 and never published.
The full design is kept in [`../history/v0.1-design.md`](../history/v0.1-design.md).

## Context

hone-flow started from the research in [`../history/0000-research.md`](../history/0000-research.md): two
hand-written pipeline runners in a song-to-video project kept "done" flags that ignored changed inputs,
duplicated their step lists, re-implemented `--from` / `--until` / `--force`, dropped review notes and
batched GPU stages by hand. The tools studied (Hamilton, Dagster, Prefect, Snakemake, DVC, Kedro,
Metaflow) suggested a well-known answer: cache each step by the content of what it depends on.

## Problem

A workflow runner for slow, expensive, non-deterministic AI steps had to:

- rerun exactly the steps whose inputs, code or settings changed, and nothing else;
- survive a crash and continue without a separate "resume mode";
- run part of a workflow (one step, from a step, until a step, some items);
- pause for a person and let their edit or note drive what reruns;
- keep outputs on a local disk or S3 behind one interface;
- explain every decision to run or skip a step.

## Options

1. **"Done" flags per step**, as the old runners did: simple, but blind to changed inputs.
2. **Cache by content** (Hamilton, Dagster style): key each step by its name, version, params and the
   content hashes ("data versions") of its inputs; store outputs under their hash.
3. **Durable execution with replay** (Temporal, DBOS): record every call inside a step. Right for
   services, far heavier than a local pipeline needs.

For code changes: explicit `version=` per step, the step's source hash, or both. For metadata: SQLite or
JSON files. For the graph: inferred from parameter names, or declared with `depends_on=`.

## Decision

Option 2, with these choices (the research's recommended defaults):

- **Cache key** = sha256 of step name, step version, the params the step uses, the data versions of its
  inputs and the workflow version. "Resume is the cache": every finished step is committed at once, so
  running the same command after a crash finds those results and continues.
- **Content-addressed assets.** Outputs are stored under their sha256 (`root/ab/cd/<sha256>`), locally or
  through fsspec (S3), and deduplicated. `export` writes a readable tree, since hashes are not browsable.
- **SQLite metadata** (`.hone/flow/spans.db`, next to the span tables): runs, executions, assets, labels,
  gate decisions and cache entries, with a documented schema other tools could read.
- **Explicit versions are authoritative**; a changed source hash with an unchanged version gives a
  warning (a strict mode invalidated instead).
- **Honest non-determinism**: a cache hit on a `deterministic=False` step is labelled `reused`; `refresh`
  forces a new sample.
- **`plan`** explains, per step and item, why it runs or is reused (`input_changed:<name>`,
  `version_changed`, `param_changed:<name>`, …).
- Selection flags (`only`, `from_`, `until`, `items`, `force`, `refresh`, `cache=False`), gates whose
  edits are input changes, asset hooks for a future indexing package, breadth-first execution with
  GPU-lease batching, per-step system metrics, and records as OpenTelemetry-shaped spans.

## Consequences

What worked, and was kept by the next design: steps as plain functions with the graph inferred from
parameter names; items, params, the context, files and folders; gates; breadth-first order and GPU
batching; failure isolation; serializers; partial runs; non-deterministic labelling; secret stripping;
the metrics sampler; the CLI's shape.

What went wrong, found once the design was reviewed against real use:

- **A run could not be opened and understood on its own.** Outputs lived under hashes; the link from a
  hash back to a run, step and item existed only in SQLite. Opening one S3 location did not show what a
  run produced.
- **Continuing a run and starting a new one from old results were blurred.** The cache silently mixed
  results of unrelated runs, and `wf.rerun` was a third, different operation.
- **The metadata database did not travel with the data.** With assets on S3, the SQLite file stayed on one
  machine.
- **Garbage collection needed a policy nobody could state simply** (see D-023 below): what does
  "unreferenced" mean when every cache entry is a reference?
- **Much machinery for one feature:** cache keys, miss reasons, expiration, strict mode, selection
  objects, export and gc existed to serve the automatic cache.

These led to [0002](0002-run-folders.md).

## Migration and compatibility

This was the first design, so there was nothing to migrate from. It was never published: its stores
are not read by later versions, and there are no users to migrate.

## Implementation choices of the first build

Recorded while building, where the design was silent. Entries that still apply today are in
[`../decisions.md`](../decisions.md) (D-001 to D-004, D-008, D-018, D-020 to D-022, D-024). The entries
below applied only to this design and ended with it.

- **D-005: early cutoff.** Downstream steps reran only when their input data versions changed, so a rerun
  that produced identical bytes left downstream cached; `force`, `refresh` and `from_` forced the
  downstream too.
- **D-006: per-item keys.** A step taking `item: fk.Item` or `ctx: fk.Context` was keyed per item (it can
  read the item id); other steps keyed only on what they named, so items with identical inputs shared
  cache entries.
- **D-007: extra miss reasons.** Besides the design's list: `workflow_version_changed`, `source_changed`
  (strict mode), `retry_failed`, `interrupted`, `refreshed`, joined with `; `.
- **D-009: read-only assets.** Stored files were `0444` so a step writing to an input got
  `PermissionError` instead of corrupting the store through a hardlink (kept in 0002 for run folders);
  a cache hit whose assets were gone became a miss (`asset_missing`).
- **D-010: single names.** `Select(only="b")` meant `Select(only=("b",))`, so a bare string was not
  iterated character by character.
- **D-011: no `MetaStore` Protocol.** The design listed one, but the contract other tools relied on was the
  SQLite schema, and a Protocol with one implementation was speculative; `SqliteMetaStore(":memory:")`
  served as the test fake. Flagged as a deviation from the design: **awaiting owner review**. The question
  lapsed when 0002 removed the metadata store.
- **D-012: cache scope.** Cache hits also matched the workflow name (workflows shared one database); a
  reused row kept the producing source hash so the stale-source warning kept showing.
- **D-013: gate decisions per output version.** A decision applied to one exact output version; a
  rejection stored `{"note", "rejected_output"}` so the same words on a new output still invalidated the
  gated step.
- **D-014: asset hooks.** `on_asset_committed` / `on_asset_deleted` received an `AssetEvent`; hook errors
  were logged, not raised, because the asset was already durable.
- **D-015: export layout.** `<item>/<step>/<name>.json`, files and folders under their own names, global
  steps under `_global/`.
- **D-016: strict mode.** The cache key never included the source hash; strict mode compared source hashes
  at lookup (reason `source_changed`), so turning it on did not invalidate everything.
- **D-017: skipped gates.** A gate outside the selection still applied its decisions, so selections could
  not bypass a review; `plan` showed `blocked_by_gate` behind a gate that would run.
- **D-019: spans in the metadata database.** Spans went to the same SQLite file by default, one span per
  (item, step) including skipped and blocked ones, with gate decision spans.
- **D-023: what gc kept.** The outputs of the latest successful execution of every (workflow, step, item),
  of each workflow's latest run, of pinned and running runs, and every asset a gate decision referred to;
  everything else was deleted. Flagged as a policy choice: **awaiting owner review**. The question lapsed
  when 0002 replaced gc with `cleanup` of whole run folders.
- **D-025: when `GateBlocked` was raised.** Only for `only=` selections that needed an unreviewed gate
  output; other selections recorded `blocked_by_gate`.
- **D-026: refresh changed the seed.** A refreshed step mixed the run id into its seed so a seeded model
  produced a new sample (0002 keeps this rule for forks).
- **D-027: content capture and the metadata tables.** With `HONE_CAPTURE_CONTENT=0`, spans held only hashes
  of params and notes, but the metadata tables kept the text (reruns and `review_note` needed it). Flagged:
  **awaiting owner review**. In 0002 the capture switch applies to spans, and run folders keep params,
  secret-stripped.
