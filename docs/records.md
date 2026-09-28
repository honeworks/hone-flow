# Records

Every call on a run leaves **spans** in the run folder's `spans.jsonl`: what ran, for which item, with
which version, inputs, outputs and result. The spans follow the honeworks records spec (OpenTelemetry
field names) and share one trace with the packages your steps call, so a tool like hone-lens can explain
a bad output from its run folder alone.

Example: [`records_and_traces.py`](../examples/records_and_traces.py).

## `spans.jsonl`

`<run>/spans.jsonl` holds one span per line, as a JSON object:

```json
{"trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "span_id": "00f067aa0ba902b7",
 "parent_span_id": "a3ce929d0e0e4736", "name": "hone.flow.step", "kind": "internal",
 "start_time": "2026-09-27T14:03:11.120Z", "end_time": "2026-09-27T14:03:13.402Z",
 "status": {"code": "ok", "message": ""},
 "attributes": {"hone.schema_version": "1", "hone.run_id": "20260927T140311Z-3f9a1c",
                "hone.step": "shotlist", "hone.item": "01", "hone.flow.status": "done", "...": "..."},
 "events": [], "resource": {"service.name": "hone-flow", "hone.package": "hone-flow",
                            "hone.package.version": "0.1.0", "host.name": "studio", "process.pid": 4242},
 "links": []}
```

Local storage appends to the file after each step commit; remote storage rewrites the object after each
step commit and at the end of the call (the run lease guarantees one writer). A crash loses at most the
spans of the steps that were running. `run.spans()` reads them back; any JSONL reader works too.
`service.name` comes from `$OTEL_SERVICE_NAME` when set.

## Span names

| Span | One per |
|---|---|
| `hone.flow.run` | run, resume or fork call; its status is the run status at the end of the call |
| `hone.flow.step` | step and item the call touched: ran, reused by a fork, or marked `skipped`, `blocked`, `pending` or `interrupted` |
| `hone.flow.gate` | the same for a gate step, and one more for every review decision |

Steps a call leaves alone (done earlier, still waiting for a review) get no span in that call. Step spans
are children of their call's run span; review decision spans are roots in the run's trace. A span's status
is `error` for a failed step (message: the exception's last line, plus an `exception` event with
`exception.stacktrace`) and for a run span whose run `failed` or was `interrupted`.

## Attributes

| Attribute | On | Meaning |
|---|---|---|
| `hone.schema_version` | all | `"1"` |
| `hone.run_id` | all | the run id |
| `hone.step`, `hone.item` | step and gate spans | the step, and the item (absent for a global step) |
| other keys of the caller's trace context (e.g. `hone.lens.finding_id`) | all spans of the call | copied from `trace=` |
| `hone.flow.workflow`, `hone.flow.workflow_version` | all | |
| `hone.flow.status` | all | step state, or the run status on `hone.flow.run` |
| `hone.flow.params` | run spans; steps that ran or were reused | JSON: the run's params, or the params the step declares |
| `hone.flow.seed` | run spans; steps that ran or were reused | the run seed, or the seed the step attempt used (`ctx.seed`) |
| `hone.flow.fork_of` | the run span of a fork | the source run id |
| `hone.flow.run.label` | run spans of a labelled run | the run's label at the end of the call |
| `hone.flow.step_version`, `hone.flow.source_hash` | step and gate spans | the step definition |
| `hone.flow.deterministic` | step and gate spans | bool |
| `hone.flow.resource` | step and gate spans | the `resources` tag, e.g. `"gpu:ollama"` |
| `hone.flow.attempt` | steps that ran or were reused; decisions | 1-based attempt number (retries and revisions count up) |
| `hone.flow.inputs`, `hone.flow.outputs` | steps that ran or were reused | JSON name → sha256 of the input / output files |
| `hone.flow.labels` | steps that ran or were reused | JSON list: `reused`, `approved`, `edited` |
| `hone.flow.reused_from` | steps a fork copied | the source run id |
| `hone.flow.gate.decision`, `hone.flow.gate.actor`, `hone.flow.gate.note` | decision spans | `approved` / `edited` / `rejected`, who, the note |
| `hone.flow.gate.actor_kind` | decision spans | `person`, or `automated` (`approve` / `edit` / `reject(..., automated=True)`) |
| `system.cpu.utilization.mean`, `system.memory.usage.peak_mb`, `hone.flow.process.memory.peak_mb`, `system.disk.io.read_bytes`, `system.disk.io.write_bytes`, `hone.flow.gpu.memory.peak_mb`, `hone.flow.gpu.utilization.mean` | step spans, with system [measurements](measurements.md) | the summary of the samples |

Events: `metrics.sample` on step spans (every sampling interval, the same metric keys), `exception` on
failed steps, and `warning` on `hone.flow.run` with `{"kind": "source_changed_version_unchanged", "step",
"old", "new", "at"}` when a resumed step's source changed without a version bump. There are no
`hone.flow.cache.*` attributes: reuse is explicit (a fork), and why a fork reran each step is in its
manifest (`fork_of.plan`).

```python
import json
import tempfile
from pathlib import Path

import hone_flow as fk

tmp = Path(tempfile.mkdtemp())
wf = fk.Workflow("trace_demo", storage=tmp / "flows")


@wf.step()
def answer(question: str) -> str:
    return question.upper()


@wf.gate()
def check(answer: str) -> str:
    return answer


run = wf.run([fk.Item("q1", {"question": "why is the sky blue?"})])
for span in run.spans():
    print(span["name"], span["attributes"].get("hone.step", ""), span["attributes"]["hone.flow.status"])
names = [s["name"] for s in run.spans()]
assert names == ["hone.flow.step", "hone.flow.gate", "hone.flow.run"]  # a span is written when it ends
step_span = run.spans()[0]
assert json.loads(step_span["attributes"]["hone.flow.outputs"]).keys() == {"answer"}
assert step_span["attributes"]["hone.flow.attempt"] == 1
```

## One trace across packages

A run's `trace_id` is chosen by `wf.run`: from `trace=` (a trace context such as
`{"traceparent": "00-<trace id>-<parent span id>-01"}`), else from the active context (a run started
inside another package's traced call), else a new one. It is recorded in the manifest.

- `resume()` continues the run's trace, with a new `hone.flow.run` span per call.
- A fork starts its own trace (or joins the caller's) and links its run span to the source run's trace.
- Steps run with the context set to their own span. A step passes `ctx.current_trace()` (or
  `fk.current_trace()`) to the packages it calls, and their spans join the trace under the step's span:

```python
def other_package_call(prompt: str, *, trace: dict[str, str]) -> dict[str, str]:
    """Stands in for e.g. hone_models' client.complete(..., trace=...), which records its own span."""
    _, trace_id, parent_span_id, _ = trace["traceparent"].split("-")  # W3C traceparent
    return {"name": "hone.models.chat", "trace_id": trace_id, "parent_span_id": parent_span_id}


model_spans: list[dict[str, str]] = []
linked = fk.Workflow("linked", storage=tmp / "flows")


@linked.step()
def reply(question: str, ctx: fk.Context) -> str:
    model_spans.append(other_package_call(question, trace=ctx.current_trace()))
    return "blue light scatters more"


caller = {
    "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    "hone.lens.finding_id": "f1",
}
run = linked.run([fk.Item("q1", {"question": "why is the sky blue?"})], trace=caller)
run_span = next(s for s in run.spans() if s["name"] == "hone.flow.run")
step_span = next(s for s in run.spans() if s["name"] == "hone.flow.step")
assert run_span["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"  # joined the caller's trace
assert run_span["parent_span_id"] == "00f067aa0ba902b7"
assert step_span["attributes"]["hone.lens.finding_id"] == "f1"  # extra context keys are copied
assert model_spans[0]["trace_id"] == run_span["trace_id"]
assert model_spans[0]["parent_span_id"] == step_span["span_id"]  # the nested call sits under the step
```

## Sending spans elsewhere too

Spans always go to `spans.jsonl`. `fk.Workflow(..., sink=...)` also sends every span to another
`RecordSink` (the family sinks: `fk.JsonlSpanSink(path)`, `fk.SqliteSpanSink(path)`, `fk.MemorySink()`,
`fk.NullSink()`, or your own, see [adapters](adapters.md#recordsink)). A sink never raises into the run: a
failing sink is logged once and its further failures are counted.

```python
sink = fk.MemorySink()
mirrored = fk.Workflow("mirrored", storage=tmp / "flows", sink=sink)


@mirrored.step()
def echo(text: str) -> str:
    return text


run = mirrored.run([fk.Item("01", {"text": "hi"})])
assert [s["span_id"] for s in sink.spans] == [s["span_id"] for s in run.spans()]
```

## Content capture and secrets

- **Secrets are never recorded.** Secret-looking values (`sk-…`, `Bearer …`) and the values of the
  environment variables named by notification destinations are replaced by `***` in spans, manifests,
  metadata, tracebacks, reports and CLI output.
- **Content capture** is on by default. With `HONE_CAPTURE_CONTENT=0` the content attributes
  (`hone.flow.params`, `hone.flow.gate.note`) are replaced by their sha256 and length in `spans.jsonl`
  (the family sinks also take `capture_content=False`). Run-folder metadata keeps the params
  (secret-stripped), because resume and fork need them.

```python
import os

os.environ["HONE_CAPTURE_CONTENT"] = "0"
private = fk.Workflow("private", storage=tmp / "flows")


@private.step()
def greet(name: str, greeting: fk.Param[str]) -> str:
    return f"{greeting} {name}"


run = private.run([fk.Item("01", {"name": "Ana"})], params={"greeting": "hello"})
run_span = next(s for s in run.spans() if s["name"] == "hone.flow.run")
assert sorted(json.loads(run_span["attributes"]["hone.flow.params"])) == ["len", "sha256"]
assert run.manifest["params"] == {"greeting": "hello"}
del os.environ["HONE_CAPTURE_CONTENT"]
```
