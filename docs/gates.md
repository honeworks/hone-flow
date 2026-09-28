# Gates

A gate is a human checkpoint inside a workflow. The run stops at the gate for each item, the process may
exit, and later (in any process, even one without the workflow's code) a person approves, edits or
rejects each item. The decision is recorded in the run folder and drives what runs next.

Examples: [`review_gates.py`](../examples/review_gates.py), [`read_api.py`](../examples/read_api.py),
[`video_pipeline.py`](../examples/video_pipeline.py).

## Declaring a gate

`@wf.gate(version="1")` registers a gate. It must take at least one step output as input: the steps whose
outputs it takes are its **producers**, and the gate reviews their outputs. The gate function runs like a
step (usually it just returns its input), then its state becomes `awaiting_review`. Steps downstream of
the gate stay `pending`, and the call ends normally with run status `awaiting_review`.

A producer that declares a parameter named `review_note` receives the latest rejection note (`None` when
it was never rejected).

```python
import tempfile

import hone_flow as fk

wf = fk.Workflow("review_demo", storage=tempfile.mkdtemp())


@wf.step(deterministic=False)
def shotlist(line: str, ctx: fk.Context, review_note: str | None = None) -> dict:
    lighting = "dark" if review_note and "dark" in review_note else "bright"
    return {"shots": [f"{line}, {lighting} light"], "attempt": ctx.attempt, "note": review_note}


@wf.gate()
def review_shotlist(shotlist: dict) -> dict:
    return shotlist


@wf.step()
def render(review_shotlist: dict) -> str:
    return " | ".join(review_shotlist["shots"])


items = [
    fk.Item("01", {"line": "city at night"}),
    fk.Item("02", {"line": "empty beach"}),
    fk.Item("03", {"line": "rooftop"}),
]
run = wf.run(items)
assert run.status == "awaiting_review"
assert run.steps("render", "01")[0].status == "pending"
```

## Decisions

Open the run (`wf.open_run(run_id)`, or `fk.open_runs(storage, name).open_run(run_id)` without the
workflow's code) and decide per item:

| Call | Effect |
|---|---|
| `run.approve(step, item, *, note="", actor=None)` | the gate becomes `done` with label `approved`; `resume()` continues downstream |
| `run.edit(step, item, *, value, actor=None)` | the gate's output is moved to `attempts/<n>/` (status `replaced`), `value` becomes its output (label `edited`, state `done`); `resume()` continues downstream with the edited value |
| `run.reject(step, item, *, note, actor=None)` | the producers are sent back with `note` (details below); `resume()` reruns them and the gate pauses again |

`step` is the gate's name. An edited value must be storable as an output; when the gate returns a
Pydantic model, the value is validated against that model (a dict is fine). `actor` defaults to `$USER`.

```python
run = wf.open_run(run.run_id)  # e.g. the next morning, in another process
run.approve(step="review_shotlist", item="01", note="good", actor="ana")
run.edit(step="review_shotlist", item="02", value={"shots": ["empty beach, drone shot"]}, actor="ana")
run.reject(step="review_shotlist", item="03", note="make it dark and moody", actor="ana")
assert run.status == "awaiting_review"  # decisions change steps; the status changes on resume

run.resume()  # 01 and 02 render; 03's shotlist reruns with the note; its gate pauses again
assert run.output("render", "02") == "empty beach, drone shot"
assert run.output("shotlist", "03") == {
    "shots": ["rooftop, dark light"],
    "attempt": 2,
    "note": "make it dark and moody",
}
assert run.status == "awaiting_review"
```

## Reject and revise

`reject(step, item, note=...)`:

1. saves the note (secret-looking values replaced by `***`);
2. moves the gate's output and each producer's output to their `attempts/<n>/` folders, with attempt
   status `rejected` and the note;
3. sets the gate and its producers `pending`, and every step downstream of the producers for that item
   `pending` too, moving their outputs to `attempts/` with status `replaced`, so no stale result survives.

On `resume()` each producer reruns as a new attempt, with a new seed (a new sample), and receives the
note through `review_note` if it declares it (a producer without the parameter reruns too). Then the gate
pauses again. A second rejection passes the latest note. A **global** producer reruns once for the run,
and its downstream steps become `pending` for every item.

```python
run.approve(step="review_shotlist", item="03")
run.resume()
assert run.status == "completed"

gate = run.steps("review_shotlist", "03")[0]
assert [r["decision"] for r in gate.reviews] == ["rejected", "approved"]
producer = run.steps("shotlist", "03")[0]
assert (producer.attempt, producer.review_note) == (2, "make it dark and moody")
assert producer.attempts[0]["status"] == "rejected"
assert producer.attempts[0]["note"] == "make it dark and moody"
assert run.steps("review_shotlist", "02")[0].labels == ["edited"]
assert run.steps("review_shotlist", "02")[0].attempts[0]["status"] == "replaced"
```

## Revising part of an output

A note often names only part of an output ("scene 4 fails to render"). A producer that declares a
parameter named `previous` receives its own latest rejected output for this item (loaded like
`run.output`, so a Pydantic model comes back as the model; several `outputs=` come as a tuple), or `None`
on a first attempt. It can keep what passed and redo only what the note names. A step retired as
`replaced` (downstream of a rejected producer) receives its replaced output the same way.
`ctx.previous_output()` returns the same value on demand. A fork starts from scratch, so there `previous`
is `None`. The value is read from the step's `attempts/<n>/output/`, which is already in the run folder.

```python
revise = fk.Workflow("revise", storage=tempfile.mkdtemp())


@revise.step()
def scenes(count: int, review_note: str | None = None, previous: dict | None = None) -> dict:
    if previous is None:
        return {str(n): f"draft {n}" for n in range(1, count + 1)}
    return {
        n: (f"fixed {n}" if n in (review_note or "").split(",") else code) for n, code in previous.items()
    }


@revise.gate()
def render_check(scenes: dict) -> dict:
    return scenes


job = revise.run([fk.Item("ep", {"count": 3})])
job.reject(step="render_check", item="ep", note="2")
job.resume()
assert job.output("scenes", "ep") == {"1": "draft 1", "2": "fixed 2", "3": "draft 3"}
```

## Where decisions are recorded

- In the gate's `metadata.json` `reviews` list: `decision` (`approved`, `edited`, `rejected`), `actor`,
  `actor_kind`, `note`, `at` and `attempt` (the gate attempt that was reviewed). `actor` defaults to
  `$USER`; pass `automated=True` to `approve` / `edit` / `reject` (CLI `--automated`) when a program (a
  render check, a model) decided, and `actor_kind` is `automated` instead of `person`. `labels` holds `approved` or `edited`.
- As a `hone.flow.gate` span in the run's `spans.jsonl`, in the run's trace, with
  `hone.flow.gate.decision`, `hone.flow.gate.actor`, `hone.flow.gate.note` and `hone.flow.attempt`
  ([records](records.md)).
- Rejected and replaced outputs stay under `attempts/<n>/output/`, so you can see what was sent back and
  why.

```python
decisions = [
    s["attributes"]["hone.flow.gate.decision"]
    for s in run.spans()
    if s["name"] == "hone.flow.gate" and "hone.flow.gate.decision" in s["attributes"]
]
assert sorted(decisions) == ["approved", "approved", "edited", "rejected"]
```

A decision takes the [run lease](run-folders.md#the-run-lease) like any change, so it raises
`fk.RunLocked` while another process is running the run. It does not change the run status and sends no
notification: the status (and its notification) is set by the next `resume()`.

## Detached reviews

A review tool does not need the workflow's code: a run opened with `fk.open_runs` can take decisions
(the manifest's step table says which steps feed a gate). Only `resume()` needs the workflow. The CLI
does the same with `hone-flow approve / edit / reject --storage ... --name ...` ([CLI](cli.md)).

```python
second = wf.run([fk.Item("04", {"line": "harbour"})])
detached = fk.open_runs(wf.storage, "review_demo").open_run(second.run_id)
detached.approve(step="review_shotlist", item="04", actor="dashboard")
assert detached.steps("review_shotlist", "04")[0].reviews[0]["actor"] == "dashboard"
wf.open_run(second.run_id).resume()  # the workflow's code continues it
assert second.status == "completed"
```

## Errors

| Situation | Error |
|---|---|
| the step is not a gate, `item` is missing, or the gate is not `awaiting_review` for that item | `fk.ReviewError` |
| another live process holds the run | `fk.RunLocked` |
| an edited value does not validate against the gate's Pydantic type, or cannot be stored | `fk.ReviewError` |
| the run is a pinned archive | `fk.HoneFlowError` (fork it instead) |

```python
try:
    run.approve(step="render", item="01")
except fk.ReviewError as exc:
    print(exc)  # 'render' is not a gate of run ...; gates: ['review_shotlist']
else:
    raise AssertionError("render is not a gate")
```

## Gates in a fork

A fork that reuses a gate copies its decision: the reused gate keeps its `approved` / `edited` label and
its `reviews`. A gate that reruns (it or one of its producers changed) pauses again for a new decision.
