# 0002: Run folders, resume and fork, notifications

## Status

`implemented in 0.1.0`. Supersedes [0001](0001-initial-design.md). The design it produced is
[`../current.md`](../current.md).

## Context

The first design ([0001](0001-initial-design.md)) was built in full: content-addressed assets, a SQLite
metadata database and an automatic cache across runs, with 195 passing tests. Reviewing it against the
way the pipeline it was built for is actually used, the owner wrote a revised run design. Its starting
point was simple: a person debugging an AI pipeline opens a folder and wants to see what a run did. The
first design could not give them that.

hone-flow was not yet published, so the rethink could replace the first design outright, keeping the
version at 0.1.0.

## Problem

- **Runs could not be opened on their own.** Outputs were stored under content hashes; mapping them back to
  a run, a step and an item needed the SQLite database. On S3 there was no single place that showed what
  a run produced.
- **Continuing and re-deriving were one blurred operation.** The automatic cache reused results of any
  earlier run with a matching key, so "finish this run" and "make a new run from these results" were not
  distinguishable, and a run could silently mix outputs made by different code.
- **The database did not travel with the data.** With outputs on S3, the metadata stayed in a local file.
- **No separation between projects.** All workflows shared one store and one database.
- **Nobody heard when a run ended**, and a human rejection note had no clear rule for which step
  received it.
- **Much machinery served one feature:** cache keys, miss reasons, expiration, strict mode, selection
  objects, `plan`, `rerun`, `export`, `gc` and asset hooks existed for the cache.

## Options

1. **Keep the cache and add a readable export.** Leaves the source of truth in hashes and SQLite; the
   export is a second copy that can drift.
2. **Run folders with references.** Each run gets a readable folder, but reused outputs and inputs point
   at files in other runs. Cheap in storage, but a folder is no longer self-contained: deleting one run
   can break another.
3. **Self-contained run folders with copies.** Each run folder holds copies of everything it used and
   produced. Reuse is an explicit operation (a fork) that copies results. Costs storage for large media.

## Decision

Option 3. The owner's rule: **copy data, don't reference it; storage is cheap** ("S3 is cheap"). Inputs are
real snapshots of what each step received, a fork copies the outputs it reuses (server-side on S3, a
hardlink or copy locally), and pinning copies the whole run. Every run folder is self-contained.

The decisions, as agreed with the owner:

1. **Workflow identity and location.** `fk.Workflow(name, *, storage, version="1", ...)`. `storage` is the
   workflow's storage root, a local path or a URL (`s3://bucket/prefix`, any fsspec URL; `memory://` in
   tests). Runs live at `<storage>/<name>/runs/<run_id>/`, pinned copies at
   `<storage>/<name>/pinned_runs/<run_id>/`. The owner's proposal named these settings `workflow_name`
   and `s3_path`; `name` and `storage` were chosen because one setting works for local disks and S3.
   Storage and name are recorded in the manifest, so a run never moves when the settings change.
2. **Run folders are the source of truth.** No content-addressed store, no metadata database, no automatic
   cross-run cache. Outputs have readable names and extensions; hashes only check integrity.
3. **Resume and fork are different operations.** `run.resume()` finishes the same run (pending, failed,
   interrupted work) and never mixes step versions: a step with work left whose version changed raises
   `IncompatibleRun`, telling the user to fork. A changed source with an unchanged version is a warning.
   `run.fork()` makes a new run beside the source. With `refresh=("lyrics",)` it refreshes that step and
   everything downstream; with no `refresh` it compares each step with the current workflow (version,
   source hash, params the step uses, content of external inputs) and reruns the changed steps and their
   downstream, copying the rest, labelled `reused`. `fork(dry_run=True)` returns the plan with a reason per
   row. Incremental reruns now happen inside a run's family tree only; this replaces the cache.
4. **A commit protocol.** A step writes its outputs first and `metadata.json` last (the commit marker);
   the manifest is updated with conditional writes; a run lease (`lease.json`, with a heartbeat) stops two
   processes from changing one run, and a stale lease is taken over, marking running steps `interrupted`.
5. **Records live in the run folder**: spans in `<run>/spans.jsonl`, with the same span names and trace
   context as before, so traces still link across the libraries a step calls. An optional `sink=` still
   receives them.
6. **Attempts.** The current result is in `output/`; retries and rejected attempts move to
   `attempts/<n>/`, and failed attempts keep their traceback.
7. **Reject and revise** (an open point in the owner's proposal). A gate reviews the output of the steps
   it takes as input. `reject(note)` keeps the rejected attempt, marks the producer pending, and on resume
   the producer reruns and receives the latest note through a parameter named `review_note`; then the
   gate pauses again. `approve` continues; `edit` stores the edited value as the gate's output.
8. **Pinning copies** a finished run to `pinned_runs/`, verifies every file's hash and marks it pinned;
   listings show one logical run. `cleanup(keep_last=, older_than=)` deletes only under `runs/`, never a
   pinned copy or a run with a live lease.
9. **Notifications** to Slack, Mattermost, Discord and any HTTP endpoint, for `run.completed`,
   `run.failed` and `run.awaiting_review`. The run status and a pending event are committed before
   delivery and the result is recorded after, so an outage never fails a run; delivery is at least once,
   with a stable event id; `run.retry_notifications()` retries on request (there is no background
   worker). Webhook URLs come only from the environment and are never recorded.
10. **Measurements** chosen with `measure=` (timing and output sizes by default; CPU, memory, disk and GPU
    on request), in each step's metadata and in `reports/`.
11. **A public read API** (`fk.open_runs`, `RunHistory`, `RunSummary`, `StepRecord`) and a documented,
    versioned run format (`format_version: "1"`), so tools read runs without the workflow's code.
12. **Kept from the first design:** the DAG from parameter names, items, params, the context, files and
    folders, the three decorators, breadth-first and depth-first order, failure isolation, GPU lease
    batching, serializers (now with readable file names), partial runs (`until`, item filters),
    non-deterministic labelling, secret stripping, tracebacks and the CLI.
13. **Removed:** the content-addressed stores, the SQLite metadata store, cache keys and every cache
    option, `plan` for fresh runs, `fk.Select`, `wf.rerun` (a step rerun is now a fork), `export` (the run
    folder is the export), `gc` (now `cleanup`), and the asset hooks (deferred; notifications are a
    different thing: run events for people, not per-file events).
14. **One small storage port**, `RunStorage` (read, write with conditions, copy, list, delete, exists),
    with local, fsspec and in-memory implementations.
15. **Out of scope** for this version: dynamic fan-out, safe side effects, a concurrency pool, a web UI,
    remote workers and scheduling, forking into another workflow or storage path.

Further choices made while writing the detailed design:

- `order` and `fail_fast` live on `Workflow`, not on `wf.run`, so resume and fork use the same settings.
- `RunStorage` also has `upload` / `download` for streamed file transfer and a `url` attribute; `if_match`
  takes a content sha256, the same for every backend.
- `fk.open_runs(storage, name)` returns a `RunHistory` with the same `runs` / `open_run` / `cleanup` as
  `Workflow`; a detached `Run` can read, review, pin and retry notifications, but not resume or fork.
- `Run` reads its files on every access: no stale snapshots and no `reload()`.
- Step state `skipped` (outside the caller's selection) and run status `partial`; downstream of an
  unreviewed gate stays `pending` (no separate "blocked by gate" state).
- A rejection also resets every step downstream of the producers, so no stale output survives; a global
  producer resets all items.
- The workflow version is part of compatibility: resume raises on a change; fork reruns every step.
- Resume's source-change warning is a `warning` event on the run span.
- Notifications use the standard library; every call ending in a notifying status emits an event;
  retries happen only on request.
- A fork never copies attempt folders; a reused step records `source_attempt`.
- `cleanup` needs at least one of `keep_last` / `older_than`; when both are given, both must hold.
- `fk.testing.WebhookServer` is public, so users can test their notification setup.

Some of these are listed for the owner's review in [`../decisions.md`](../decisions.md#awaiting-owner-review).

## Consequences

Better:

- Open one location, local or S3, and see everything a run used and produced, with readable names.
- Projects and workflows live under separate paths.
- Continuing and re-deriving are explicit and different; a run never silently mixes code versions, and
  every rerun in a fork has a stated reason.
- A crash loses at most the running steps, and any process can take the run over.
- People hear when a run ends; reviewers' notes reach the step that must revise.
- Less machinery: the cache, its options and the metadata database are gone.

Costs and new responsibilities:

- Inputs and reused media are stored again in every run that uses them.
- The storage path and workflow name must stay stable for existing runs.
- Fork compatibility has to be computed and explained.
- Metadata and files need a careful commit protocol, and S3 lacks some atomic operations (the run lease
  covers the gap).
- Notifications bring credentials, retries and possible duplicate messages.
- Without a database, listing many thousands of runs is slow; an index can be added later without being
  needed to run a workflow.

## Migration and compatibility

- Stores written by the first design (content-addressed assets, `.hone/flow/spans.db` metadata) are not
  read by this version, and there is no migration tool.
- The first design was never published, so there are no users or data to migrate. The package version
  stayed at 0.1.0.
- Removed API, with no aliases: `wf.plan`, `wf.rerun` (use `run.fork(refresh=...)`), `fk.Select`, `cache=`,
  `cache_expiration`, `strict_versions`, `wf.status`, `wf.approve` / `edit` / `reject` (now on `Run`),
  `wf.export`, `wf.gc` (now `cleanup`), `wf.pin` (now `run.pin()`), the asset hooks, `AssetStore` and its
  stores, `SqliteMetaStore`, `MemoryAssetStore`, `MemoryMetaStore`, `GateBlocked`, `AssetNotFound`, and the
  CLI commands `plan`, `export` and `gc`.
- Span changes for readers of hone-flow records: flow spans are in each run's `spans.jsonl` instead of a
  SQLite file; the `hone.flow.cache.*` attributes are gone; `hone.flow.reused_from`, `hone.flow.fork_of`
  and `hone.flow.attempt` are new.
- The rebuild removed the obsolete code, tests and docs first, then reused the parts listed in decision
  12. Where kept modules had been built on the old runner, only the parts that work on plain values were
  kept (D-029 in [`../decisions.md`](../decisions.md)).
