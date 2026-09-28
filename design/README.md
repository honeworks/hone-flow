# Why hone-flow exists

This folder explains the design of hone-flow: the problem it solves, the ideas it is built on, how it
works today and how the design got here. The user documentation is in [`docs/`](../docs/index.md).

## The problem

AI workflows are chains of slow, expensive and non-deterministic steps: write lyrics with a language
model, turn them into a shot list, render keyframes on a local GPU, assemble a video. Such a chain fails
in ordinary ways (a crash at step 3 of 4, a bad output a person must fix, a model server that is down),
and each failure hurts, because rerunning from scratch costs hours of GPU time and gives different
samples.

Hand-written runners for this work collect the same bugs:

- **"Done" flags that ignore changes.** A step marked done in a `state.json` is not rerun when its input
  (new lyrics) changes, unless someone remembers `--force`.
- **Step lists that drift.** The list of steps is repeated in the runner, the state file and the CLI, and
  one of them falls behind.
- **Hand-made partial runs.** `--from`, `--until` and `--force` are rewritten for every pipeline.
- **Review notes nothing reads.** A person rejects an output with a note, and no step ever sees it.
- **Manual stage batching.** All language-model work, then all image work, then all video work, so each
  model loads once, arranged by hand.
- **No way to see what a run used.** Outputs are scattered, paths are strings, and nobody can say which
  inputs, code and settings produced a file.
- **Silent endings.** An overnight run finishes or fails and nobody notices.

## Why existing tools fall short

- **Airflow, Prefect, Dagster, Flyte, ZenML** are built around servers, schedulers and deployments: too
  heavy for a local tool or a script.
- **Hamilton** has a fine dataflow-of-functions model, but no steps over items with human pauses.
- **Snakemake and DVC pipelines** have good rerun triggers, but think in files and shell commands.
- **Kedro** slices pipelines well, **Metaflow** has artifacts and `resume`, but each ties you to its own
  classes and infrastructure.
- **Temporal and DBOS** give durable execution with replay: right for services, more than a local AI
  pipeline needs.
- **Burr and LangGraph** persist state and pause for humans, but are aimed at agents.

None of them combines plain-function steps, fan-out over items, a readable record of every run on a
local disk or S3, explicit resume and fork, human checkpoints and GPU-aware batching, without a server.

## Core ideas

1. **Steps are plain typed functions.** The graph comes from parameter names: a parameter named after a
   step receives that step's output. There is one workflow definition, and every command reads it.
2. **A run is a folder.** Every run is a self-contained folder, `<storage>/<name>/runs/<run_id>/`, local
   or on S3: copies of what each step received, outputs with readable names, and documented JSON
   metadata. Run folders are the only state: no database, no content-addressed store.
3. **Commit per step.** A step writes its outputs first and its `metadata.json` last, so a crash loses at
   most the steps that were running.
4. **Resume and fork are different operations.** Resume finishes the same run and never mixes step
   versions. Fork makes a new run beside the old one, copies what did not change and explains, step by
   step, why it reruns the rest.
5. **A human review is a persisted pause.** A gate stops the run; a person approves, edits or rejects
   with a note, possibly days later from another process, and the producing step receives the note.
6. **Evidence everywhere.** Each run holds its spans (`spans.jsonl`), its measurements, its reports and
   every attempt, including failed and rejected ones. Reused results are labelled `reused`, never passed
   off as fresh samples.
7. **Nothing wakes up by itself.** No server and no scheduler: a library and a command line. Notifications
   are sent when a call ends, and retried only on request.

## What it deliberately does not do

- It is not an orchestration platform: no always-on server, no scheduler, no remote workers, no web UI.
- It does not replay code inside a step. A step that crashes halfway runs again from its start; long
  steps keep their own checkpoints.
- It has no automatic cross-run cache. Reuse is explicit, through a fork of a run you chose.
- It does not turn a step's list output into new items during a run (dynamic fan-out), protect external
  side effects such as payments from running twice, or run steps concurrently. These may come later as
  design changes.
- It is not an agent framework: no loops driven by model decisions.

## What is in this folder

| File | What it holds |
|---|---|
| [`current.md`](current.md) | the design as it stands today: concepts, rules and the guarantees the tests check |
| [`changes/0001-initial-design.md`](changes/0001-initial-design.md) | the first design: content-addressed assets, SQLite metadata, an automatic cache (superseded) |
| [`changes/0002-run-folders.md`](changes/0002-run-folders.md) | the rethink: run folders, resume and fork, notifications |
| [`history/0000-research.md`](history/0000-research.md) | the research that started the project |
| [`history/v0.1-design.md`](history/v0.1-design.md) | the first design in full, kept readable |
| [`decisions.md`](decisions.md) | small implementation choices, and the items awaiting owner review |

A new design change starts as a record in `changes/` with status `proposed`; see
[CONTRIBUTING.md](../CONTRIBUTING.md#changing-the-design). How the package was built is described in the
[README](../README.md#how-this-was-built).
