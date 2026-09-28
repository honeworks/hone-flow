# Research: a workflow runner for AI pipelines (2026-09)

This is the research that started hone-flow, rewritten from the original project brief. It records the
problem as first seen, the tools studied, the first ideas and the open questions. The first design built
from it is [v0.1](v0.1-design.md); the design today is [`../current.md`](../current.md).

**Names at the time.** The brief used placeholder names: `flowkit` is today's **hone-flow**; the sister
projects `bestofn`, `modelkit` and `tracelens` became hone-select, hone-models and hone-lens; record
fields prefixed `ours.flow.*` became `hone.flow.*`.

## The idea

A small, standalone Python library and CLI that runs multi-step workflows whose steps are user code, and
handles everything around them:

- running steps in the right order, for each item (for example each song);
- keeping step outputs on a local disk or on S3, behind one interface;
- continuing after a crash or interruption;
- partial runs: one step, from a step, until a step, force a step;
- reusing a step's output when its inputs have not changed, with a switch to turn that off;
- pausing for a person to review or edit, then continuing;
- recording what ran, why, with which inputs, and how long it took.

It is meant for AI and generative work: slow, expensive, sometimes non-deterministic steps, often on a
local GPU, where rerunning from scratch hurts. The pitch: *"Write your steps as plain functions; get
caching, resume, partial runs, S3 or local assets, and human checkpoints without running an orchestration
server."*

## Where the need came from

The first user was a local song-to-video pipeline with two hand-written runners. Their problems are the
requirements:

| Problem in the existing runners | What the runner should do |
|---|---|
| `state.json` stores "step done" flags, so a changed input (new lyrics) reruns nothing unless someone remembers `--force` | notice changed inputs and code, and rerun exactly what depends on them |
| the list of steps is duplicated in three files; they drifted and one command crashed on unknown step names | one workflow definition that every command reads |
| `--from` / `--until` / `--force` re-implemented per pipeline | built-in selection |
| a `regen --note` stores a note that nothing reads | a human decision that drives the right steps |
| stage batching by hand (all language-model work, then all image work, then video) | resource tags and breadth-first execution across items |
| outputs scattered across folders, with paths stored as strings | one storage interface and a record of every output |
| no record of why a step ran or was skipped | an explanation for every decision |

## The tools studied

- **Airflow, Prefect, Dagster, Flyte, ZenML:** powerful, but built around servers, schedulers and
  deployments; too heavy for a local tool or a script.
- **Hamilton:** excellent caching (code version plus data version per node), but a dataflow of functions,
  not steps over items with human pauses.
- **Snakemake and DVC pipelines:** good rerun triggers, but centred on files and shell commands.
- **Kedro:** good pipeline slicing and data catalog, but no content-addressed step caching.
- **Metaflow:** content-addressed artifacts and `resume`, but tied to its flow classes and infrastructure.
- **Temporal and DBOS:** durable execution with replay; right for services, heavier than needed here.
- **Burr and LangGraph:** state machines with persistence and human pauses, aimed at agents.

Nothing small combined plain-function steps, fan-out over items, assets on a local disk or S3, caching
that is honest about non-determinism, partial runs, human checkpoints and resource-aware batching.

## First ideas

**Concepts.** A *workflow* is a named, versioned DAG of *steps* (user functions) run over *items*. A
*run* is one execution with a run id and a trace id. Step outputs are *assets*. A *gate* pauses for a
person. A *resource tag* (`gpu:ollama`, `cpu`) says what a step needs.

**The graph from parameter names** (Hamilton style): a parameter named after another step receives its
output, so there is no separate YAML to drift. This idea survived into today's design.

**Caching by content.** Key = hash(step name, step version, params used, data versions of the inputs),
where a data version is the content hash of an asset. Changing lyrics changes `timeline`'s data version,
which changes `shotlist`'s key, and so on down the graph.

**Detecting code changes** is the hardest part, and every tool does it differently (Prefect hashes only
the function's own lines, Snakemake has a code trigger, Dagster uses an explicit `code_version`, Hamilton
hashes node source). The recommendation: an explicit `version=` per step is authoritative, and a changed
source hash with an unchanged version gives a warning instead of silently rerunning or silently reusing.

**Non-deterministic steps.** For a language-model step, a cache hit means "reuse the earlier output", not
"this is what you would get again", and the records must say so. The seed is a normal parameter.
Model id and version should be inputs of the step, because changing the model often fails to invalidate
caches.

**Resume is caching.** Commit each step's result as soon as it finishes; running the same command after
a crash then continues from the first incomplete step. This is step-level durability, not replay inside a
step: a step that crashes halfway runs again from its start.

**Crash-safe writes.** Write to a temporary path, `fsync`, rename into place, then commit the metadata.

**Storage.** One interface over local and remote storage through fsspec; content-addressed so identical
outputs are stored once; an `export` command for a readable tree, "because content-addressed storage is
not browsable"; large files streamed; garbage collection with pins.

**Execution.** In-process, no server. Breadth-first by stage, so all steps on one model run together and
each model loads once; failures isolated per item.

**Human checkpoints.** A gate pauses its items; approve, edit (in `$EDITOR`) or reject with a note. "An
edit is just an input change." Gate only irreversible or expensive steps, not every step.

**Records.** Every step execution records its ids, version, source hash, cache decision and reason,
inputs, outputs, params, timings, resource tag, GPU wait, status, errors and system metrics, as
OpenTelemetry-shaped spans. An analysis tool should later be able to rerun a step for chosen items with
overridden params.

**Hooks for a future indexing package:** events when assets are committed or deleted, a stable metadata
schema, typed assets and labels such as `approved`.

**Best practices to enforce:** one workflow definition; cache by content, not "done" flags; explicit
versions with source-change warnings; honest non-determinism; commit each result as soon as it finishes;
crash-safe writes; explain every decision; per-item failure isolation; human edits as input changes;
content-addressed, exportable assets; no server and no scheduler; secrets never stored.

## Open questions, and how they were answered

| Question | Recommended then | What happened |
|---|---|---|
| Code-change detection | explicit versions plus source-hash warnings | adopted, and kept today: resume refuses a version change, fork reruns on `source_changed` |
| How the DAG is defined | inferred from parameter names | adopted and kept |
| Default execution order | breadth-first | adopted and kept |
| Metadata store | SQLite (or JSON files for transparency) | SQLite in [v0.1](v0.1-design.md); replaced by JSON files in run folders in [0002](../changes/0002-run-folders.md) |
| Where metadata lives when assets are on S3 | local only for v1 | the weak point of v0.1; solved in 0002 by keeping all metadata in the run folder |
| Name | `flowkit` was a placeholder | **hone-flow** |
| License and Python | MIT or Apache-2.0; Python 3.11+ | Apache-2.0; Python 3.11+ |

Planned for later at the time: dynamic fan-out, a worker pool per resource tag, in-step checkpoints,
OpenTelemetry export, a web view, remote executors and scheduling. None of these is in 0.1.0.

## References

- Caching and code versioning: [Hamilton caching](https://hamilton.apache.org/concepts/caching/),
  [Dagster asset versioning](https://docs.dagster.io/guides/dagster/asset-versioning-and-caching),
  [Prefect caching](https://docs.prefect.io/v3/concepts/caching),
  [Snakemake rerun triggers](https://snakemake.readthedocs.io/en/stable/executing/cli.html),
  [DVC pipelines](https://doc.dvc.org/user-guide/pipelines/running-pipelines).
- Partial runs, catalogs, artifacts: [Kedro slicing](https://docs.kedro.org/en/stable/build/slice_a_pipeline/),
  [Metaflow technical overview](https://docs.metaflow.org/internals/technical-overview),
  [fsspec](https://filesystem-spec.readthedocs.io/).
- Resume and durability: [DBOS Transact](https://github.com/dbos-inc/dbos-transact-py).
- Human checkpoints: [LangGraph human-in-the-loop](https://docs.langchain.com/oss/python/langchain/human-in-the-loop),
  [Burr state persistence](https://burr.apache.org/docs/concepts/state-persistence/).
- Caching non-deterministic LLM steps: [AI21 on caching in agentic pipelines](https://www.ai21.com/blog/caching-in-agentic-llm-pipelines/).
