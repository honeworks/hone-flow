# hone-flow

[![CI](https://github.com/honeworks/hone-flow/actions/workflows/ci.yml/badge.svg)](https://github.com/honeworks/hone-flow/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](https://github.com/honeworks/hone-flow/blob/main/LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)

Run AI workflows of plain-function steps as self-contained run folders, with resume, fork, partial runs
and human gates.

Part of **[honeworks](https://github.com/honeworks)**: small, standalone tools for reliable generative-AI
workflows. Works on its own; works better with its siblings.

## Why

Generative-AI pipelines are slow, costly and only partly deterministic: a model call fails on item 37 of
50, a prompt changes after a review, a person has to approve a draft before the next step spends GPU
time on it. Scripts and notebooks lose that state; orchestration platforms bring services to run and
maintain. hone-flow sits in between: a library that keeps everything in plain run folders.

You write each step as a plain typed Python function. hone-flow wires the steps together from their
parameter names and runs them over a list of items (songs, documents, products). Every run is a folder,
local or on S3, that you can open and understand on its own: what each step received and produced, which
code and settings produced it, how long it took, what a person decided, and what failed. A crashed or
failed run is resumed where it stopped; a finished run is forked to rerun only what changed. There is no
server, scheduler or database: a library and a command line.

## Install

```bash
pip install hone-flow          # or: uv add hone-flow
```

Until the first release is on PyPI, install from GitHub:

```bash
pip install "git+https://github.com/honeworks/hone-flow"
pip install "hone-flow[cli,s3] @ git+https://github.com/honeworks/hone-flow"   # with extras
```

The core needs only pydantic. Optional extras:

| Extra | Adds | Pulls in |
|---|---|---|
| `cli` | the `hone-flow` command | typer |
| `s3` | run folders on S3 or any fsspec filesystem | fsspec, s3fs |
| `metrics` | CPU, memory and disk measurements | psutil |
| `gpu` | GPU memory and utilization measurements | nvidia-ml-py |

`pip install "hone-flow[cli,s3,metrics]"` installs several at once. Python 3.11 or newer.

## Quickstart

```python
import tempfile

import hone_flow as fk

wf = fk.Workflow("hello", storage=tempfile.mkdtemp())


@wf.step()
def words(text: str) -> list[str]:  # `text` is an item input
    return text.split()


@wf.step()
def shout(words: list[str], mark: fk.Param[str]) -> str:  # `words` is the output of step `words`
    return " ".join(w.upper() for w in words) + mark


run = wf.run(
    [fk.Item("a", {"text": "hello world"}), fk.Item("b", {"text": "good night"})], params={"mark": "!"}
)
print(run.status, run.output("shout", "a"))
print("run folder:", run.location)
assert run.status == "completed"
assert run.output("shout", "b") == "GOOD NIGHT!"
assert shout(["plain", "call"], "?") == "PLAIN CALL?"  # still a normal function
```

This is [`examples/quickstart.py`](https://github.com/honeworks/hone-flow/blob/main/examples/quickstart.py). A parameter named after another step
receives that step's output; any other name is an item input; `fk.Param[...]` marks a run-level setting.

## A run folder

```text
<storage>/hello/
  runs/20260927T140311Z-3f9a1c/
    manifest.json            the run: items, params, steps, state of every step, calls
    spans.jsonl              what happened, as OpenTelemetry-shaped spans
    reports/                 timing.json  output_sizes.json  summary.md
    words/item_a/            metadata.json  inputs/text.json  output/words.json
    words/item_b/            ...
    shout/item_a/            metadata.json  inputs/words.json  output/shout.json
    shout/item_b/            ...
  pinned_runs/               verified copies of runs you want to keep
```

`metadata.json` is written last, so a step folder that has one is complete. The JSON files are a
public, versioned format ([docs/run-format.md](https://github.com/honeworks/hone-flow/blob/main/docs/run-format.md)); read them with any tool.
More: [run folders](https://github.com/honeworks/hone-flow/blob/main/docs/run-folders.md).

## Main ideas

- **Resume**: `run.resume()` continues the same run after a failure, a crash or a partial call, and runs
  only the work left. It refuses (`IncompatibleRun`) when a step with work left changed its version.
  [Resume and fork](https://github.com/honeworks/hone-flow/blob/main/docs/resume-and-fork.md).
- **Fork**: `run.fork(refresh=("shotlist",))` makes a new run beside the old one, reruns that step and its
  downstream and copies the rest. Without `refresh` it finds what changed (versions, source, params,
  input files) and explains every rerun; `dry_run=True` shows the plan. [Resume and fork](https://github.com/honeworks/hone-flow/blob/main/docs/resume-and-fork.md).
- **Partial runs**: `wf.run(items, until="step", items_filter=["01"])` runs part of the work now; the run
  is `partial` until you resume it. [Resume and fork](https://github.com/honeworks/hone-flow/blob/main/docs/resume-and-fork.md#partial-runs).
- **Gates**: `@wf.gate()` pauses each item for a person, who approves, edits or rejects with a note that
  the producing step receives as `review_note` (with its rejected output as `previous`). [Gates](https://github.com/honeworks/hone-flow/blob/main/docs/gates.md).
- **Across items**: `@wf.final_step()` combines every item's result (a workbook from all lessons), a
  global step returning `fk.Items` makes the items (chapters of an outline) and `@wf.select_step()`
  chooses mid-run which items continue. [Steps across items](https://github.com/honeworks/hone-flow/blob/main/docs/across-items.md).
- **Progress and labels**: `on_event=` and `INFO` logs follow a long run while it runs; `label=` names a
  run for people. [Concepts](https://github.com/honeworks/hone-flow/blob/main/docs/concepts.md#progress-while-a-run-runs), [Read API](https://github.com/honeworks/hone-flow/blob/main/docs/read-api.md#run-labels).
- **Notifications**: Slack, Mattermost, Discord or any HTTP endpoint hears when a run completes, fails or
  waits for review; an outage never fails a run. [Notifications](https://github.com/honeworks/hone-flow/blob/main/docs/notifications.md).
- **Measurements**: timing and output sizes by default, CPU, memory, disk and GPU on request, in each
  step's metadata and in `reports/`. [Measurements](https://github.com/honeworks/hone-flow/blob/main/docs/measurements.md).
- **Storage**: a local folder, `s3://bucket/prefix`, any fsspec URL, or your own `RunStorage`.
  [Storage](https://github.com/honeworks/hone-flow/blob/main/docs/storage.md).
- **Records**: spans in each run's `spans.jsonl`, in one trace with the packages your steps call.
  [Records](https://github.com/honeworks/hone-flow/blob/main/docs/records.md).
- **CLI**: `hone-flow run / resume / fork / status / show / runs / label / approve / edit / reject /
  pin / cleanup / notify-retry`, each with `--json`. [CLI](https://github.com/honeworks/hone-flow/blob/main/docs/cli.md).
- **Read API**: `fk.open_runs(storage, name)` lists and opens runs without the workflow's code.
  [Read API](https://github.com/honeworks/hone-flow/blob/main/docs/read-api.md).

## Use with the rest of honeworks

hone-flow needs no other honeworks package. When you have them:

- **Trace context.** A step passes `ctx.current_trace()` to the packages it calls
  (`client.complete(..., trace=ctx.current_trace())` with hone-models, a selection with hone-select), so
  their spans join the run's trace under the step's span. [Records](https://github.com/honeworks/hone-flow/blob/main/docs/records.md#one-trace-across-packages).
- **GPU scheduling.** `fk.Workflow(..., gpu="hone_models")` uses hone-models' `GpuLease` through the
  `hone.gpu_leases` entry point, so workflow steps and model calls share one GPU plan.
  [Adapters](https://github.com/honeworks/hone-flow/blob/main/docs/adapters.md#gpulease).
- **hone-lens** reads runs through `fk.open_runs` and the run format, and reruns a step as a fork.
  [Read API](https://github.com/honeworks/hone-flow/blob/main/docs/read-api.md).

## Documentation

- [docs/](https://github.com/honeworks/hone-flow/blob/main/docs/index.md): concepts, one page per feature, the CLI, adapters, the run format.
- [examples/](https://github.com/honeworks/hone-flow/blob/main/examples/README.md): 27 small runnable scripts, one per concept, in reading order.

## Design and contributing

- [design/](https://github.com/honeworks/hone-flow/blob/main/design/README.md): why hone-flow exists, [the design today](https://github.com/honeworks/hone-flow/blob/main/design/current.md), and one
  [record per design change](https://github.com/honeworks/hone-flow/tree/main/design/changes/) with what was found, decided and why.
- [CONTRIBUTING.md](https://github.com/honeworks/hone-flow/blob/main/CONTRIBUTING.md): setup, the quality gates, conventions and how to propose a change.

## How this was built

hone-flow was specified by a human and built by AI coding agents (Claude) working against written
specifications and acceptance tests; a human reviewed the decisions they made, and commits written with
AI carry a `Co-Authored-By` line. Every design change, with what was found, what was decided and why, is
in [design/changes/](https://github.com/honeworks/hone-flow/tree/main/design/changes/); the smaller implementation choices,
including those still awaiting the owner's review, are in [design/decisions.md](https://github.com/honeworks/hone-flow/blob/main/design/decisions.md).

## Status

Alpha, version 0.1.0. The run-folder format is versioned (`format_version` "1") and documented; the
Python API may still change between minor versions before 1.0. Changes are listed in
[CHANGELOG.md](https://github.com/honeworks/hone-flow/blob/main/CHANGELOG.md). Bug reports and ideas are
welcome in the [issues](https://github.com/honeworks/hone-flow/issues).

## License

[Apache-2.0](https://github.com/honeworks/hone-flow/blob/main/LICENSE). Copyright 2026 Bahman Shadmehr.
