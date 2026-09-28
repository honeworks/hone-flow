# Read API

Tools that only know where runs are stored (hone-lens, a dashboard, a script, a future memory package)
read them with `fk.open_runs`, without importing the workflow's code. This page lists the names and fields
exactly as the code has them; hone-lens' flow adapter builds on it. The files underneath are documented in
[run-format.md](run-format.md).

Example: [`read_api.py`](../examples/read_api.py).

## `fk.open_runs(storage, name) -> RunHistory`

`storage` is what `fk.Workflow(storage=...)` takes (a path, a URL or a `RunStorage`); `name` is the
workflow name. Nothing is read until you call a method.

| `RunHistory` | Returns |
|---|---|
| `runs(*, updated_since=None)` | `list[RunSummary]`: one per logical run, newest first (by `created_at`); reads every manifest |
| `run_ids()` | `list[str]`: every run id, newest first, from one-level listings of `runs/` and `pinned_runs/`; reads no manifest |
| `summaries(run_ids)` | `list[RunSummary]` of those runs, in that order, reading only their manifests (in parallel on remote storage); ids without a manifest are left out |
| `open_run(run_id)` | a detached `Run`: `runs/<id>` if it exists, else the pinned copy; `fk.RunNotFound` if neither |
| `cleanup(*, keep_last=None, older_than=None, dry_run=False)` | `CleanupReport(deleted, kept, locked)`, as `wf.cleanup` ([storage](storage.md#pinning-and-cleanup)) |

`updated_since` is an ISO-8601 string (`"2026-09-01T00:00:00Z"`) or a `datetime`; a time without a
time zone is UTC. It keeps the runs whose manifest `updated_at` is **at or after** it. Every change to a
run (a call, a step commit, a review, a pin, a notification result) rewrites the manifest, so a tool can
poll: remember the largest `updated_at` it has seen and pass it next time. A string that is not ISO-8601
raises `fk.HoneFlowError`.

`wf.runs(updated_since=...)`, `wf.open_run(run_id)` and `wf.cleanup(...)` are the same methods on a
workflow; runs opened that way are attached (they can also resume and fork).

```python
import shutil
import tempfile
import time

import hone_flow as fk

storage = tempfile.mkdtemp()


def produce() -> None:
    """Somewhere else, some other day: the code that makes the runs."""
    wf = fk.Workflow("reports", storage=storage)

    @wf.step()
    def summarize(text: str) -> dict:
        if not text:
            raise ValueError("empty document")
        return {"words": len(text.split())}

    @wf.gate()
    def check(summarize: dict) -> dict:
        return summarize

    wf.run([fk.Item("doc-1", {"text": "a b c"}), fk.Item("doc-2", {"text": ""})])


produce()
time.sleep(0.01)
since = fk.open_runs(storage, "reports").runs()[0].updated_at
produce()

history = fk.open_runs(storage, "reports")  # no workflow code from here on
assert len(history.runs()) == 2
newest = history.runs(updated_since=since)[0]
print(newest)
assert (newest.workflow, newest.status, newest.items) == ("reports", "failed", ("doc-1", "doc-2"))
```

## `RunSummary`

A frozen dataclass, one per logical run:

| Field | Type | Meaning |
|---|---|---|
| `run_id` | `str` | the run id |
| `workflow` | `str` | the workflow name |
| `workflow_version` | `str` | the workflow version that created the run |
| `status` | `str` | `running`, `completed`, `failed`, `awaiting_review`, `partial`, `interrupted` |
| `created_at` | `str` | ISO-8601 UTC, milliseconds |
| `updated_at` | `str` | the last manifest write |
| `fork_of` | `str \| None` | the source run id of a fork |
| `pinned` | `bool` | a verified copy exists under `pinned_runs/` |
| `items` | `tuple[str, ...]` | the item ids |
| `location` | `str` | the run folder: the `runs/` copy while it exists, else the pinned copy |
| `label` | `str \| None` | the run's human name ([run labels](#run-labels)); `None` when unset |
| `description` | `str \| None` | a longer description; `None` when unset |
| `attempts` | `Mapping[str, int]` | `"<step>/<item>"` (or `"<step>"`) → attempt count, for steps tried more than once |

## Large histories: a page at a time

`runs()` reads every manifest, which is right for a script and slow for a table of thousands of runs on
S3. A run table asks for the ids first (one-level listings; ids start with their creation time, so they
sort newest first, to the second) and reads the manifests of one page:

```python
ids = history.run_ids()
page = history.summaries(ids[:50])
assert {s.run_id for s in page} == {s.run_id for s in history.runs()}
retried = {s.run_id: s.attempts for s in page}  # steps tried more than once, from the manifest alone
```

`summaries` also reads runs you already know the ids of (for example ids a tool stored). A running run
whose process died still says `running` until another call takes its lease over: `run.lease()` tells
them apart (`live` is false for a holder that is gone).

```python
assert history.open_run(newest.run_id).lease() is None  # nobody holds a finished run
```

## Detached `Run`

`history.open_run(run_id)` returns a `fk.Run` without the workflow's code. A `Run` holds no cached
state: every property and method reads the files when called, so it never shows a stale status.

| Member | Detached | Meaning |
|---|---|---|
| `run_id`, `workflow`, `location` | yes | the id, the workflow name, the run folder's URL or path |
| `status`, `manifest`, `pinned` | yes | from `manifest.json` (`manifest` is the parsed JSON) |
| `steps(step=None, item=None)` | yes | `list[StepRecord]`, in workflow order (global steps once, item steps per item) |
| `output(step, item=None, *, name=None, local_dir=None)` | yes | the current output (see below) |
| `spans()` | yes | the spans of `spans.jsonl` as dicts ([records](records.md)) |
| `approve`, `edit`, `reject` | yes | review decisions ([gates](gates.md)) |
| `lease()` | yes | `fk.LeaseInfo(host, pid, acquired_at, heartbeat_at, expires_at, live)` of the process holding the run, or `None` |
| `set_label(label, description=None)` | yes | name the run ([run labels](#run-labels)) |
| `pin()`, `retry_notifications()` | yes | [pinning](storage.md#pinning-and-cleanup), [notifications](notifications.md) |
| `resume(...)`, `fork(...)` | no: `fk.HoneFlowError` | they need the workflow: use `wf.open_run(run_id)` |

`output(step, item)` returns the value of the step's current output: a Pydantic model (when its class can
be imported, else a dict), JSON data, a registered serializer's value, or an `fk.File` / `fk.Dir` with a
local path (on remote storage downloaded into `local_dir`, or a temporary folder). `item=None` is for a
global step. A step with several outputs needs `name=`. A step without a current output (not run,
failed, or sent back by a rejection) raises `fk.OutputNotFound`.

```python
run = history.open_run(newest.run_id)
for record in run.steps():
    print(f"{record.step}/{record.item}: {record.status}", record.error["message"] if record.error else "")
assert [r.status for r in run.steps("summarize")] == ["done", "failed"]
assert run.output("summarize", "doc-1") == {"words": 3}

run.approve(step="check", item="doc-1", actor="dashboard")  # detached runs take reviews
try:
    run.resume()
except fk.HoneFlowError as exc:
    print(exc)  # resume needs the workflow's code: use wf.open_run(...)
else:
    raise AssertionError("a detached run cannot resume")
try:
    run.output("summarize", "doc-2")
except fk.OutputNotFound as exc:
    print(exc)  # step 'summarize' has no current output for item 'doc-2' ... (failed)
```

## `StepRecord`

A frozen dataclass per step and item. It comes from the step's `metadata.json` when the step has a
folder, else from the manifest's `state` (a `pending`, `skipped` or `blocked` step has no folder: then
`attempt` is `0` and the dicts and lists are empty). The dicts and lists are the plain JSON of
[run-format.md](run-format.md#metadatajson-one-per-step-folder).

| Field | Type | Meaning |
|---|---|---|
| `step` | `str` | the step name |
| `item` | `str \| None` | the item id; `None` for a global step |
| `kind` | `str` | `step`, `gate` or `global` |
| `status` | `str` | the step state ([concepts](concepts.md#step-states-and-run-statuses)) |
| `version` | `str` | the step version that ran (or is recorded) |
| `source_hash` | `str` | sha256 of the step function's source (empty when unavailable) |
| `attempt` | `int` | the latest attempt, 1-based; `0` when the step never started |
| `attempts` | `list[dict]` | earlier attempts: `attempt`, `status` (`failed`, `rejected`, `replaced`), `started_at`, `ended_at`, `note`, `error`, `outputs` |
| `labels` | `list[str]` | `reused`, `approved`, `edited` |
| `reused_from` | `str \| None` | the source run id when a fork copied this result |
| `reviews` | `list[dict]` | gate decisions: `decision`, `actor`, `note`, `at`, `attempt` |
| `review_note` | `str \| None` | the latest rejection note the step received |
| `params` | `dict` | the run params the step declares (secret-stripped) |
| `inputs` | `dict` | per parameter: `from` and `files` (name → `sha256`, `size`) under `inputs/` |
| `outputs` | `dict` | per output name: `type` and `files` under `output/` |
| `error` | `dict \| None` | `message` and `traceback` of a failed latest attempt |
| `started_at`, `ended_at` | `str \| None` | the latest attempt's times |
| `duration_ms` | `int \| None` | the latest attempt's wall time |
| `measurements` | `dict` | `timing`, `output_sizes`, `system` ([measurements](measurements.md)) |
| `location` | `str` | the step folder's URL or path, ending in `/` |

```python
blocked = run.steps("check", "doc-2")[0]
assert (blocked.status, blocked.attempt, blocked.outputs) == ("blocked", 0, {})
failed = run.steps("summarize", "doc-2")[0]
assert failed.error["message"] == "ValueError: empty document"
assert failed.location.endswith(f"/reports/runs/{run.run_id}/summarize/item_doc-2/")
```

## Copies and versions

A run folder is self-contained: a copy of it elsewhere (another disk, another bucket) is readable the same
way. Readers ignore keys they do not know; a `format_version` this hone-flow does not know raises
`fk.HoneFlowError` asking to upgrade hone-flow.

```python
elsewhere = tempfile.mkdtemp()
shutil.copytree(f"{storage}/reports", f"{elsewhere}/reports")
copied = fk.open_runs(elsewhere, "reports")
assert {s.run_id for s in copied.runs()} == {s.run_id for s in history.runs()}
assert copied.open_run(newest.run_id).output("summarize", "doc-1") == {"words": 3}
```

## Run labels

A run id says when a run started; a **label** says what it is ("Rain on a tin roof", "Leads
2026-09-28"). Labels are plain optional data: nothing depends on them, and runs made before labels
existed simply have none (`None`), so a tool falls back to its own naming.

- `wf.run(items, ..., label=None, description=None)` names a run from the start; `run.fork(...,
  label=, description=)` names a fork (default: `"<source label> (fork)"` and the source's description).
- `ctx.set_run_label(label, description=None)` names the run from inside a step, for names that only a
  step knows (a title it wrote). It applies when that step's result is committed; a failed attempt
  changes nothing.
- `run.set_label(label, description=None)` renames a run later, attached or detached. `None` (or blank
  text) removes the label; `description=None` keeps the current description. It takes the run lease like
  a review: `fk.RunLocked` while a process runs the run. A pinned archive refuses it.
- The label is in `manifest.json` (`label`, `description`), `RunSummary`, the `hone.flow.run` span
  (`hone.flow.run.label`), notification messages and `hone-flow runs` / `hone-flow label`.
  Secret-looking text is stored as `***`.

```python
labels = fk.Workflow("labelled", storage=tempfile.mkdtemp())


@labels.global_step()
def idea(ctx: fk.Context) -> str:
    ctx.set_run_label("Rain on a tin roof", description="one song")
    return "rain"


named = labels.run([fk.Item("a")], label="draft")
assert (named.manifest["label"], named.manifest["description"]) == ("Rain on a tin roof", "one song")
named.set_label("Rain (final)")
listed = fk.open_runs(labels.storage, "labelled").runs()[0]
assert (listed.label, listed.description) == ("Rain (final)", "one song")
```
