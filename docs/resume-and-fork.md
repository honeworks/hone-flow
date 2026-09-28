# Resume and fork

A run can be continued in two ways, and they are deliberately different:

| | `run.resume()` | `run.fork()` |
|---|---|---|
| Run id | the same run | a new run beside the source (`runs/<new id>/`) |
| Runs | the work left: `pending`, `failed`, `interrupted`, `blocked`, `skipped` | what changed since the source, what you `refresh`, and their downstream |
| Done steps | never recomputed | copied into the new run (label `reused`) |
| Workflow changes | refused (`IncompatibleRun`) if they would mix step versions | expected: that is what the fork diff is for |
| Params, items, seed | the run's recorded ones | the source's, with your changes merged over them |

Examples: [`failure_and_resume.py`](../examples/failure_and_resume.py),
[`crash_recovery.py`](../examples/crash_recovery.py),
[`incompatible_resume.py`](../examples/incompatible_resume.py),
[`partial_runs.py`](../examples/partial_runs.py), [`fork_refresh.py`](../examples/fork_refresh.py),
[`fork_diff.py`](../examples/fork_diff.py).

## Resume

`run.resume(*, until=None, items=None, trace=None)` continues the same run id. It takes the
[run lease](run-folders.md#the-run-lease) (taking over a stale one and cleaning up after the dead
process), then runs every step that is `pending`, `failed`, `interrupted`, `blocked` or `skipped`, with the
run's recorded params, items and seed. A retried failed step records attempt `n + 1`; its earlier attempts
stay in `metadata.json`. Gates keep waiting unless a person decided. `resume()` returns the run itself.

To resume in another process, open the run with the workflow that made it: `wf.open_run(run_id)`.

```python
import logging
import tempfile

import hone_flow as fk

storage = tempfile.mkdtemp()


def make(polish_version: str, *, fixed: bool) -> fk.Workflow:
    """The same workflow as two releases of our code define it."""
    wf = fk.Workflow("versions", storage=storage)

    @wf.step()
    def draft(text: str) -> str:
        return text

    if fixed:

        @wf.step(version=polish_version)
        def polish(draft: str) -> str:
            return draft + "!"

    else:

        @wf.step(version=polish_version)
        def polish(draft: str) -> str:
            raise RuntimeError("polish is broken")

    return wf


run = make("1", fixed=False).run([fk.Item("01", {"text": "hello"})])
assert run.status == "failed"
```

### Resume never mixes versions

Before running anything, resume compares the workflow in memory with the run's manifest. It raises
`fk.IncompatibleRun` and runs nothing when:

- the workflow `version` differs;
- steps were added or removed, or a step's inputs or outputs changed;
- a step that still has work to do has a different `version` than the manifest records.

The message names the steps and versions and says "fork the run instead: run.fork()". Done steps are
never recomputed by resume, whatever their version.

```python
try:
    make("2", fixed=True).open_run(run.run_id).resume()
except fk.IncompatibleRun as exc:
    print(exc)  # ... step 'polish' version 1 -> 2 and it still has work to do ... run.fork()
else:
    raise AssertionError("resume should refuse")
```

When a step with work left changed its **source** but not its version, resume continues, logs a warning
(logger `hone_flow`), appends `{"kind": "source_changed_version_unchanged", "step", "old", "new", "at"}`
to the manifest's `warnings`, and adds a `warning` event to the call's `hone.flow.run` span.

```python
logging.getLogger("hone_flow").setLevel(logging.ERROR)  # keep this page's output quiet
fixed = make("1", fixed=True).open_run(run.run_id)
fixed.resume()
assert fixed.status == "completed" and fixed.output("polish", "01") == "hello!"
assert fixed.manifest["warnings"][0]["kind"] == "source_changed_version_unchanged"
```

Other refusals: a run opened without its workflow (`fk.open_runs(...).open_run(id)`) raises
`HoneFlowError` ("resume needs the workflow's code: use wf.open_run(...)"), and so does a pinned archive
("fork it instead"). An item file that changed or disappeared since `wf.run` fails the step that reads
it; restore the file, or fork the run.

## Partial runs

Run part of the work now and the rest later:

| Call | Runs |
|---|---|
| `wf.run(items, until="step")` | that step and everything it needs |
| `wf.run(items, items_filter=["01"])` | only those items |
| `run.resume(until="step", items=["02"])` | the same selection, on the remaining work |
| `run.resume()` | everything left |

Work outside the selection is `skipped` and the run's status is `partial`.

```python
calls: list[str] = []
partial = fk.Workflow("partial", storage=tempfile.mkdtemp())


@partial.step()
def outline(topic: str, ctx: fk.Context) -> str:
    calls.append(f"outline/{ctx.item_id}")
    return f"outline of {topic}"


@partial.step()
def article(outline: str, ctx: fk.Context) -> str:  # the expensive step
    calls.append(f"article/{ctx.item_id}")
    return outline.replace("outline", "article")


items = [fk.Item("01", {"topic": "tides"}), fk.Item("02", {"topic": "moons"})]
run = partial.run(items, until="outline")
assert run.status == "partial" and run.steps("article", "01")[0].status == "skipped"
run.resume(items=["02"])
assert calls[-1] == "article/02" and run.status == "partial"
run.resume()
assert calls[-1] == "article/01" and run.status == "completed"
```

## Fork

`run.fork(refresh=(), *, items=None, params=None, dry_run=False, trace=None)` creates a new run from the
source run and the **current** workflow definition, then executes it like `wf.run` (stopping at gates and
failures). The source is only read; a fork works even while another process holds the source (only its
committed `done` steps are reused), and on a pinned archive.

- `refresh=("shotlist",)` reruns `shotlist` and everything downstream of it (a single name as a string
  works too). `refresh={"shotlist": ["02"]}` reruns it for item `02` only and copies the other items
  (CLI: `--refresh shotlist=02`); final steps that consume it rerun too
  ([steps across items](across-items.md)).
- `fork()` with no `refresh` reruns exactly what changed since the source, and its downstream.
- `params` are merged over the source's params.
- `items=None` keeps the source's items. Otherwise pass item ids (keep the source's definition) and/or
  `fk.Item` objects (a known id replaces that item's inputs; a new id adds an item). The fork contains
  only the items you list.

Reused steps are **copied**, not referenced: their `inputs/` and `output/` files are copied (a hardlink
locally, a server-side copy on S3) and their `metadata.json` is written anew with `status: done`, label
`reused` (a gate also keeps `approved` / `edited`), `reused_from: <source run id>`, `source_attempt` and
no attempts. Deleting the source leaves the fork complete. Incremental reruns happen only inside a run's
family tree; there is no cache across unrelated runs.

```python
from pathlib import Path

Path("01.md").write_text("rain on the roof\n")
Path("02.md").write_text("sun on the sea\n")
family = fk.Workflow("family", storage=tempfile.mkdtemp())


@family.step()
def timeline(lyrics: fk.File) -> list[str]:
    return lyrics.path.read_text().split()


@family.step(deterministic=False)
def shotlist(timeline: list[str], style: fk.Param[str], ctx: fk.Context) -> dict:
    return {"shots": [f"{word}/{style}" for word in timeline], "sample": ctx.seed % 1000}


@family.step()
def render(shotlist: dict) -> str:
    return " ".join(shotlist["shots"])


items = [fk.Item("01", {"lyrics": fk.File("01.md")}), fk.Item("02", {"lyrics": fk.File("02.md")})]
source = family.run(items, params={"style": "noir"})

new = source.fork(refresh=("shotlist",))
assert new.run_id != source.run_id and new.manifest["fork_of"]["run_id"] == source.run_id
reused = new.steps("timeline", "01")[0]
assert (reused.labels, reused.reused_from) == (["reused"], source.run_id)
assert new.steps("shotlist", "01")[0].reused_from is None  # computed again: a new sample
```

### The fork diff

For every step and item of the new run, the fork decides `reuse` or `run` and records why. The `run`
reasons are (several are joined with `; `):

| Reason | The step runs because |
|---|---|
| `refresh_requested` | it is named in `refresh` (for this item, with a mapping) |
| `items_changed` | a final or select step, and the fork has other items than the source |
| `produced_item_changed` | an item a step produced has other inputs in the fork ([produced items](across-items.md#items-made-by-a-step-fan-out)); decided when the producer has run |
| `version_changed 1->2` | its version differs from the source step's |
| `source_changed` | its source code differs and its version does not |
| `param_changed:<name>` | a `fk.Param` it declares has a different value |
| `input_changed:<name>` | the content (sha256) of an item input it reads differs |
| `workflow_version_changed 1->2` | the workflow version differs (applies to every step) |
| `new_step` | the step is not in the source |
| `new_item` | the item is not in the source |
| `not_done_in_source` | the source step is not `done` (failed, pending, skipped, blocked, awaiting review, interrupted) |
| `downstream_of:<step>` | an upstream step (of the same item, or a global step) runs for a reason of its own |

Everything else is `reuse` with reason `unchanged`. `downstream_of` names the nearest upstream step that
runs for its own reason.

### Dry runs

`fork(dry_run=True)` returns an `fk.ForkPlan(source_run_id, rows)` and writes nothing. Each row is an
`fk.ForkPlanRow(item, step, action, reason)`; `item` is `None` for a global step. The executed fork
follows the same plan and records it in its manifest as `fork_of.plan`.

```python
plan = source.fork(dry_run=True)
assert {row.reason for row in plan.rows} == {"unchanged"}  # nothing changed yet

Path("01.md").write_text("snow on the roof\n")  # an input file changed for item 01
plan = source.fork(params={"style": "bright"}, dry_run=True)
for row in plan.rows:
    print(f"{row.action:5} {row.step}/{row.item}: {row.reason}")
reasons = {(row.step, row.item): row.reason for row in plan.rows}
assert reasons[("timeline", "01")] == "input_changed:lyrics"
assert reasons[("timeline", "02")] == "unchanged"
assert reasons[("shotlist", "01")] == "param_changed:style; downstream_of:timeline"
assert reasons[("shotlist", "02")] == "param_changed:style"
assert reasons[("render", "02")] == "downstream_of:shotlist"

executed = source.fork(params={"style": "bright"})
assert executed.manifest["fork_of"]["plan"] == [
    {"item": r.item, "step": r.step, "action": r.action, "reason": r.reason} for r in plan.rows
]
assert executed.output("render", "01") == "snow/bright on/bright the/bright roof/bright"
```

### Items and params

```python
one = source.fork(refresh="shotlist", items=["02"], params={"style": "dark"})
assert one.manifest["params"] == {"style": "dark"}
assert [i["id"] for i in one.manifest["items"]] == ["02"]

Path("03.md").write_text("wind in the trees\n")
more = source.fork(items=["02", fk.Item("03", {"lyrics": fk.File("03.md")})], dry_run=True)
assert {(r.step, r.reason) for r in more.rows if r.item == "03"} == {
    ("timeline", "new_item"),
    ("shotlist", "new_item; downstream_of:timeline"),
    ("render", "new_item; downstream_of:shotlist"),
}
```

Item files are hashed again at their recorded original path. A file that no longer exists there counts
as unchanged, and a step that reruns and needs it receives the source run's snapshot of it:

```python
Path("02.md").unlink()
again = source.fork(refresh=("timeline",), items=["02"])
assert again.status == "completed" and again.output("timeline", "02") == ["sun", "on", "the", "sea"]
```

### Seeds and traces

A fork keeps the source's run seed, so steps that rerun for a change reproduce their sampling; steps
named in `refresh` get a new seed and produce a new sample. A fork starts its own trace (or joins the
caller's `trace=` / active context); its `hone.flow.run` span carries `hone.flow.fork_of` and a link to
the source run's trace ([records](records.md)).

## Rerunning one step

A step-level rerun is a fork. hone-lens' `StepRerunner` port is implemented exactly like this:

```python
def rerun(run_id: str, step: str, items: list[str] | None, params: dict | None) -> list[str]:
    return [family.open_run(run_id).fork(refresh=(step,), items=items, params=params).run_id]


(new_id,) = rerun(source.run_id, "render", ["01"], None)
assert family.open_run(new_id).manifest["fork_of"]["run_id"] == source.run_id
```
