# Concepts

A **workflow** is a named set of **steps**. A **run** executes the steps for a list of **items** and is
kept as a **run folder**. This page explains each of these and the rules that connect them.

Examples: [`quickstart.py`](../examples/quickstart.py),
[`params_and_context.py`](../examples/params_and_context.py),
[`global_steps.py`](../examples/global_steps.py), [`serializers.py`](../examples/serializers.py),
[`failure_and_resume.py`](../examples/failure_and_resume.py),
[`gpu_batching.py`](../examples/gpu_batching.py).

## The workflow

```python
import tempfile

import hone_flow as fk

wf = fk.Workflow("song_video", storage=tempfile.mkdtemp(), version="1")
```

`fk.Workflow` is the single place for configuration. Every option after the name is keyword-only:

| Option | Default | Meaning |
|---|---|---|
| `name` | required | stable workflow name and folder name: letters, digits, `_`, `.`, `-` |
| `storage` | required | where run folders live: a path, a URL (`s3://…`) or a `RunStorage` ([storage](storage.md)) |
| `version` | `"1"` | the workflow's version; resume refuses a different one, fork reruns everything |
| `measure` | `("timing", "output_sizes")` | what is measured per step ([measurements](measurements.md)) |
| `notifications` | `()` | webhook destinations ([notifications](notifications.md)) |
| `order` | `"breadth_first"` | or `"depth_first"` (see [order](#order-failures-and-gpu-batches)) |
| `fail_fast` | `False` | `True`: the first failing step stops the call and raises `StepFailed` |
| `gpu` | `None` | a `GpuLease`, or the name of one in the `hone.gpu_leases` entry points ([adapters](adapters.md)) |
| `sink` | `None` | an extra `RecordSink` that receives every span too ([records](records.md)) |

## Steps and items

A step is a plain function registered with a decorator. An item is one unit the workflow fans out over
(a song, a document); `fk.Item(id, inputs)` gives it an id and named inputs.

```python
from pydantic import BaseModel


class Timeline(BaseModel):
    duration: float
    lines: list[str]


@wf.step(version="1")
def timeline(lyrics: fk.File) -> Timeline:  # `lyrics` is an item input (a file)
    lines = lyrics.path.read_text().splitlines()
    return Timeline(duration=4.0 * len(lines), lines=lines)


@wf.step(version="1", resources="gpu:ollama", deterministic=False)
def shotlist(timeline: Timeline, mood: str, ctx: fk.Context) -> dict:  # `timeline` is a step output
    return {"shots": [f"{line} ({mood})" for line in timeline.lines], "seed": ctx.seed}
```

`@wf.step()` options:

| Option | Default | Meaning |
|---|---|---|
| `version` | `"1"` | bump it when the step's behaviour changes; resume and fork compare it |
| `resources` | `"cpu"` | a tag; neighbouring steps with the same `gpu:` tag share one GPU lease |
| `vram_gb` | `0.0` | GPU memory the step asks the lease for |
| `deterministic` | `True` | `False` records that the step samples (an LLM, a diffusion model) |
| `outputs` | `None` | names of several outputs; the step returns a tuple (see below) |

Item inputs are JSON values, `fk.File(path)` or `fk.Dir(path)`. Item ids name folders, so they must be
unique and non-empty, and may not be `.`, `..`, `_global` or contain `/` or `\`.

```python
from pathlib import Path

Path("01.md").write_text("city lights\nlate train\n")
Path("02.md").write_text("open sea\n")
items = [
    fk.Item("01", {"lyrics": fk.File("01.md"), "mood": "calm"}),
    fk.Item("02", {"lyrics": fk.File("02.md"), "mood": "dark"}),
]
run = wf.run(items)
assert run.status == "completed"
assert run.output("timeline", "01") == Timeline(duration=8.0, lines=["city lights", "late train"])
assert run.output("shotlist", "02")["shots"] == ["open sea (dark)"]
```

A step is still an ordinary function: call it in a test with plain arguments.

## How a step's parameters are resolved

The DAG is inferred from parameter names; there is no separate list of edges. Each parameter is resolved
by the first rule that matches:

| # | Parameter | Receives |
|---|---|---|
| 1 | annotated `fk.Context` | the [step context](#the-step-context) |
| 2 | annotated `fk.Item` | the item, every input snapshotted into the step's `inputs/` |
| 3 | annotated `fk.Param[T]` | the run param of that name (`wf.run(..., params={...})`) |
| 4 | named `review_note` | the latest rejection note of a [gate](gates.md), `None` if never rejected |
| 4 | named `previous` | the step's own latest rejected or replaced output for this item, `None` on a first attempt ([gates](gates.md#revising-part-of-an-output)) |
| 5 | named after a step (or one of its `outputs=` names) | that step's output: a dependency edge; a global step's output works the same way |
| 6 | any other name | the item input of that name (or the parameter's default) |

`wf.validate(items)` checks the wiring without running anything; every run, resume and fork checks it
first. Problems raise `fk.WorkflowDefinitionError` with the step and parameter at fault: a name that is
neither a step output, a param nor an item input, a cycle, two steps (or outputs) with one name, a gate
without a step input, an unsafe item id or workflow name, an unknown `until` step, a missing required
param, params that are not JSON, `*args` / `**kwargs`.

```python
strict = fk.Workflow("strict", storage=tempfile.mkdtemp())


@strict.step()
def summary(article: str) -> str:
    return article[:10]


try:
    strict.validate([fk.Item("01", {"text": "no article input here"})])
except fk.WorkflowDefinitionError as exc:
    print(exc)  # step 'summary' parameter 'article' is not a step output, a fk.Param, or an input ...
else:
    raise AssertionError("the missing input should be reported")
```

## Params

`fk.Param[T]` marks a run-level setting. Only the params a step declares reach it, and they are recorded
in its `metadata.json`, so it is clear what influenced each result. A default makes a param optional.

```python
@wf.step()
def poster(shotlist: dict, style: fk.Param[str], size: fk.Param[int] = 512) -> str:
    return f"{len(shotlist['shots'])} shots, {style}, {size}px"


run = wf.run(items, params={"style": "noir"})
assert run.output("poster", "01") == "2 shots, noir, 512px"
assert run.steps("poster", "01")[0].params == {"size": 512, "style": "noir"}
```

Params must be JSON values. Secret-looking values (`sk-…`, `Bearer …`) are recorded as `***`, and resume
and fork pass the recorded values, so pass secrets through the environment, not params.

## The step context

A step that declares `ctx: fk.Context` receives:

| Field / method | Meaning |
|---|---|
| `run_id`, `trace_id` | the run and its trace |
| `item_id` | the item (`"_global"` in a global step) |
| `step` | the step name |
| `attempt` | 1-based; counts retries and revisions of this step for this item |
| `seed` | a stable seed for sampling (see [seeds](#seeds)) |
| `new_file(name)`, `new_dir(name)` | a path in the attempt's local work folder; return `fk.File` / `fk.Dir` of it to store it as an output |
| `logger` | a `logging.Logger` named `hone_flow.steps.<step>` |
| `current_trace()` | the trace context to pass to other packages ([records](records.md)) |
| `gpu_lease(vram_gb=0.0, *, name=None, timeout_s=None)` | hold the workflow's GPU lease around part of the step |

## What a step may return

| Return value | Stored as | Loaded back by `run.output()` as |
|---|---|---|
| JSON data (dict, list, str, number, bool, `None`) | `output/<output name>.json` | the data |
| a Pydantic model | `output/<output name>.json` (the model's import path in metadata) | the model, or a dict if its class cannot be imported |
| `fk.File(path)` | the file under its own name | `fk.File` |
| `fk.Dir(path)` | the folder under its own name (it must not be empty) | `fk.Dir` |
| a type registered with `fk.register_serializer` | `output/<output name>.<extension>` | the value |

With `outputs=("a", "b")` a step returns a tuple and each element is a separate output that other steps
name as a parameter:

```python
import fractions

fk.register_serializer(
    fractions.Fraction,
    dump=lambda value: str(value).encode(),
    load=lambda data: fractions.Fraction(data.decode()),
    name="fraction",
    extension="frac",
)

parts = fk.Workflow("parts", storage=tempfile.mkdtemp())


@parts.step(outputs=("title", "words"))
def split(text: str) -> tuple[str, list[str]]:
    first, *rest = text.split()
    return first.title(), rest


@parts.step()
def ratio(words: list[str], title: str) -> fractions.Fraction:
    return fractions.Fraction(len(words), len(title))


run = parts.run([fk.Item("01", {"text": "moon rises over water"})])
assert run.output("split", "01", name="title") == "Moon"
assert run.output("ratio", "01") == fractions.Fraction(3, 4)
assert Path(run.location, "ratio/item_01/output/ratio.frac").read_text() == "3/4"
```

## Global steps

`@wf.global_step()` runs once per run, not per item; item steps take its output by its name. A global
step may only use `fk.Context`, `fk.Param[...]` and other global steps. A failed global step blocks
every item step that uses it.

```python
shared = fk.Workflow("shared", storage=tempfile.mkdtemp())


@shared.global_step()
def style_guide(style: fk.Param[str]) -> dict:
    return {"style": style, "palette": ["black", "amber"]}


@shared.step()
def shot(line: str, style_guide: dict) -> str:
    return f"{line} [{style_guide['style']}]"


run = shared.run([fk.Item("01", {"line": "dawn"}), fk.Item("02", {"line": "dusk"})], params={"style": "noir"})
assert run.output("style_guide") == {"palette": ["black", "amber"], "style": "noir"}  # item=None
assert run.output("shot", "02") == "dusk [noir]"
```

## Gates

`@wf.gate()` is a human checkpoint. It reviews the outputs of the steps it takes as input (its
*producers*); the run stops with status `awaiting_review` and a person approves, edits or rejects each
item. See [gates](gates.md).

## Order, failures and GPU batches

- **Breadth-first** (default): step A for every item, then step B; items in the given order.
  `order="depth_first"` takes each item through all its steps before the next item.
- **Failure isolation**: an exception marks that step and item `failed` (with its traceback), the item's
  downstream steps `blocked`, and the other items continue. With `fail_fast=True` the call stops after
  the failure is committed and raises `fk.StepFailed`.
- **GPU batches**: neighbouring steps (in order) with the same `gpu:` tag hold the workflow's GPU lease
  once for the whole batch, with the largest `vram_gb` of the batch, so a model loads once; depth-first
  takes it per item. See [`gpu_batching.py`](../examples/gpu_batching.py) and [adapters](adapters.md).

## Progress while a run runs

`wf.run`, `run.resume` and `run.fork` return when the call ends, which can be an hour later. To follow a
run while it runs:

- **Logs**: hone-flow logs each run start (with its id and folder), each step start and end, each GPU
  lease wait and the run's end at `INFO` on the `hone_flow` logger, so
  `logging.basicConfig(level=logging.INFO)` is enough for a command line tool.
- **`on_event=`**: a callable that receives the same events as small dicts, in order. Every event has
  `event`, `run_id`, `workflow` and `at`; `run_started` adds `kind` (`run`, `resume`, `fork`) and
  `location`; `step_started` adds `step`, `item` (`None` for a run-level step) and `attempt`;
  `step_finished` also `status` and `duration_ms`; `lease_waiting` adds the lease `name` and `vram_gb`,
  `lease_granted` the `name` and `wait_ms`; `run_finished` adds the run `status`. A lease that waits
  forever shows as a `lease_waiting` without its `lease_granted`. A callback that raises is logged and
  ignored: progress never changes a run.

```python
events = []
progress = fk.Workflow("progress", storage=tempfile.mkdtemp())


@progress.step()
def shout(text: str) -> str:
    return text.upper()


done = progress.run([fk.Item("a", {"text": "hi"})], on_event=events.append)
assert [e["event"] for e in events] == ["run_started", "step_started", "step_finished", "run_finished"]
assert events[0]["location"] == done.location  # known before the first step runs
```

See [`progress.py`](../examples/progress.py).

## Step states and run statuses

Each step of each item has a state, in the manifest's `state` table and in its `metadata.json`:

| Step state | Meaning |
|---|---|
| `pending` | not reached yet, waiting behind a gate, or reset by a rejection |
| `running` | running now (manifest only) |
| `done` | committed; a result copied by a fork is `done` with label `reused` |
| `failed` | raised an exception; the error and traceback are in `metadata.json` |
| `blocked` | an upstream step failed for this item |
| `skipped` | left out by `until`, `items_filter` or `items` of the call |
| `awaiting_review` | a gate waiting for a person |
| `interrupted` | was running when its process died |
| `not_selected` | a [select step](across-items.md#select-steps-choosing-which-items-continue) left this item out; final, counts as done |

The run status is set at the end of every run, resume and fork call; the first match wins:

| Run status | When |
|---|---|
| `failed` | any step `failed` (the other items still finished) |
| `awaiting_review` | any gate waiting |
| `partial` | work left `pending`, `skipped`, `blocked` or `interrupted` by the caller's selection |
| `completed` | everything `done` (or `not_selected`) |
| `running` | while a call runs |
| `interrupted` | its process died while running (shown once another process takes the run over) |

A run that stops at a gate or is partial is not an error: the call returns normally and the process may
exit. `run.status` reads `manifest.json` on every access, so it is never stale.

## Seeds

`wf.run(items, seed=0)` sets the run seed. Each step gets `ctx.seed`, derived from the run seed, the item
and the step (`int(sha256(...)[:8], 16)`): different per item and step, and the same on resume and on
retries of a failed step, so sampling is reproducible. A step rerun because a person rejected its output,
or because a fork was asked to `refresh` it, gets a new seed, so it produces a new sample.

```python
first = wf.run(items, params={"style": "noir"}, seed=7)
second = wf.run(items, params={"style": "noir"}, seed=7)
assert first.output("shotlist", "01")["seed"] == second.output("shotlist", "01")["seed"]
assert first.output("shotlist", "01")["seed"] != first.output("shotlist", "02")["seed"]
```

## Determinism labels

`deterministic=False` is recorded in the step's `metadata.json` and on its span
(`hone.flow.deterministic`). Each result also carries `labels`: `reused` (copied by a fork, not computed
in this run), `approved` or `edited` (a gate's decision). A reused sample is therefore never mistaken for
a fresh one.

```python
record = first.steps("shotlist", "01")[0]
assert record.status == "done" and record.labels == []
assert first.manifest["steps"][1]["deterministic"] is False  # the step table records it too
```

## Errors

Every error hone-flow raises on purpose is an `fk.HoneFlowError`:

| Error | Raised when |
|---|---|
| `WorkflowDefinitionError` | the steps cannot be wired, or a call's arguments do not fit the workflow |
| `StepFailed` | a step raised and the workflow has `fail_fast=True` |
| `IncompatibleRun` | `resume()` would mix step versions; fork instead |
| `RunLocked` | another live process holds the run |
| `RunNotFound` | no run with that id |
| `OutputNotFound` | the step has no current output for that item |
| `ReviewError` | a decision on a step that is not a gate, or not awaiting review |
| `WriteConflict` | a conditional storage write failed (a second writer without the lease, or a bug) |
