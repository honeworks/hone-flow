# hone-flow: the design today (0.1.0, run folders)

This is the design as it stands: what hone-flow does, the rules it follows and the behaviour it
guarantees. Why it looks like this is in [`changes/0002-run-folders.md`](changes/0002-run-folders.md);
the smaller choices are in [`decisions.md`](decisions.md). The user documentation in
[`docs/`](../docs/index.md) explains the same features with runnable examples.

Section numbers are stable: code comments refer to them (for example "design §4.4").

## 1. What hone-flow does

hone-flow runs multi-step workflows whose **steps are user code**, and keeps every run as a **run
folder** that can be opened and understood on its own: what each step received, what it produced, which
code and settings produced it, how long it took, what a person decided and what failed. There is no
server, no scheduler and no database: a library plus a command line. It is built for slow, expensive,
non-deterministic AI steps on a local GPU, with run folders on a local disk or on S3.

A run can be **resumed** (the same run id: finish pending, failed and interrupted work) or **forked** (a
new run id beside it: copy chosen results of the earlier run, recompute the rest). A human review is a
persisted pause. Run lifecycle events can be sent to Slack, Mattermost, Discord or any HTTP endpoint.

### Goals

- Steps are plain typed functions; the graph of steps (a DAG) is inferred from parameter names.
- **Run folders are the source of truth.** Every run folder is self-contained: inputs are copied
  snapshots, outputs have readable names and extensions, metadata is documented JSON.
- **Commit per step.** A crash loses at most the steps that were running; `resume()` continues.
- **Resume and fork are different operations.** Resume never mixes step versions; fork explains, step by
  step, what it reuses, what it recomputes and why.
- Human decisions (approve, edit, reject with a note) are recorded and drive the next steps.
- Every run's records (`spans.jsonl`), measurements and reports live in its folder.

### Not in this version

Safe external side effects, a concurrency pool, a web
UI, remote workers and scheduling, forking into another workflow or storage path, in-step checkpoints, an
automatic cross-run cache, and a run index database (listings read manifests).

## 2. Public API

```text
import hone_flow as fk
from hone_flow.notifications import DiscordWebhook, HttpWebhook, MattermostWebhook, SlackWebhook

wf = fk.Workflow(
    name,                                   # stable workflow name, a safe folder name: [A-Za-z0-9_.-]+
    *,
    storage,                                # str | os.PathLike | fk.RunStorage: local path, "file://…", "s3://bucket/prefix",
                                            #   any fsspec URL ("memory://…" in tests), or a RunStorage object
    version="1",                            # workflow version (recorded; see §4.4, §4.5)
    measure=("timing", "output_sizes"),     # also "cpu", "memory", "disk", "gpu"; () for none (§4.10)
    notifications=(),                       # SlackWebhook / MattermostWebhook / DiscordWebhook / HttpWebhook (§4.9)
    order="breadth_first",                  # or "depth_first" (§4.6)
    fail_fast=False,                        # True: the first step failure stops the call and raises StepFailed
    gpu=None,                               # GpuLease object or `hone.gpu_leases` entry-point name; default NullGpuLease
    sink=None,                              # optional extra RecordSink; spans always go to <run>/spans.jsonl too
)

@wf.step(version="1", *, resources="cpu", vram_gb=0.0, deterministic=True, outputs=None, per=None)
@wf.global_step(version="1", *, resources="cpu", vram_gb=0.0, deterministic=True, outputs=None)
@wf.gate(version="1", *, per=None)          # human checkpoint; reviews the step outputs it takes as input
@wf.final_step(version="1", *, resources="cpu", vram_gb=0.0, deterministic=True, outputs=None,
               partial_ok=False)            # once, after all items: item steps' outputs as dicts (§4.18)
@wf.select_step(version="1", *, resources="cpu", vram_gb=0.0, deterministic=True, partial_ok=False)
                                            # chooses the items that continue: list[str] | fk.Selection (§4.19)

wf.validate(items=None) -> None             # raises WorkflowDefinitionError
wf.run(items, *, params=None, until=None, items_filter=None, seed=0, trace=None,
       label=None, description=None, on_event=None) -> Run
wf.open_run(run_id) -> Run                  # attached: resume / fork allowed
wf.runs(*, updated_since=None) -> list[RunSummary]
wf.cleanup(*, keep_last=None, older_than=None, dry_run=False) -> CleanupReport

fk.open_runs(storage, name) -> RunHistory   # read access without the workflow's code
RunHistory.runs(*, updated_since=None) -> list[RunSummary]
RunHistory.run_ids() -> list[str]            # one-level listings, no manifest read (§4.14)
RunHistory.summaries(run_ids) -> list[RunSummary]   # only those manifests
RunHistory.open_run(run_id) -> Run          # detached: read, review, pin, retry notifications; resume / fork raise
RunHistory.cleanup(*, keep_last=None, older_than=None, dry_run=False) -> CleanupReport

class Run:
    run_id: str; workflow: str; location: str          # location = URL / path of the run folder
    status: str                                         # read from manifest.json on every access (§4.3)
    manifest: dict                                      # the parsed manifest.json (fresh read)
    pinned: bool
    def steps(self, step=None, item=None) -> list[StepRecord]
    def output(self, step, item=None, *, name=None, local_dir=None) -> Any
    def spans(self) -> list[dict]
    def lease(self) -> LeaseInfo | None                 # who holds the run, and whether it is live
    def resume(self, *, until=None, items=None, trace=None, on_event=None) -> Run   # returns self
    def fork(self, refresh=(), *, items=None, params=None, dry_run=False, trace=None,
             label=None, description=None, on_event=None) -> Run | ForkPlan
    def set_label(self, label, description=None) -> None                          # §4.16
    def approve(self, step, item=None, *, note="", actor=None, automated=False) -> None
    def edit(self, step, item=None, *, value, actor=None, automated=False) -> None
    def reject(self, step, item=None, *, note, actor=None, automated=False) -> None
    def pin(self) -> None
    def retry_notifications(self) -> list[Delivery]

fk.Item(id, inputs: Mapping[str, Any] = {})  # inputs: JSON values, fk.File(path), fk.Dir(path)
fk.Items([...])                               # a list of items a global step produces (§4.20)
fk.Param[T]                                   # annotation marking a run-level param
fk.File, fk.Dir                               # file / folder values (inputs and outputs)
fk.Context                                    # injected when a step declares `ctx: fk.Context`;
                                              #   ctx.set_run_label(label, description=None) (§4.16)
fk.register_serializer(cls, dump, load, *, name=None, extension="bin")   # dump(value) -> bytes, load(bytes) -> value
fk.RunSummary, fk.StepRecord, fk.ForkPlan, fk.ForkPlanRow, fk.CleanupReport, fk.Delivery,
fk.LeaseInfo, fk.Entry                                                       # frozen dataclasses
fk.Selection(keep, reasons={})                # a select step's decision (a Pydantic model)
fk.RunStorage (Protocol), fk.LocalStorage(root), fk.FsspecStorage(url)       # FsspecStorage needs extra `s3` (fsspec)
fk.GpuLease, fk.RecordSink, fk.TraceContext (Protocols / alias), fk.PORTS_VERSION
fk.FileLockGpuLease, fk.NullGpuLease
fk.JsonlSpanSink, fk.SqliteSpanSink, fk.MemorySink, fk.NullSink              # span sinks
fk.current_trace()
fk.errors: HoneFlowError, WorkflowDefinitionError, StepFailed, IncompatibleRun, RunLocked, RunNotFound,
           OutputNotFound, ReviewError, WriteConflict
fk.testing: MemoryStorage, FakeGpuLease, WebhookServer, contracts (check_gpu_lease, check_run_storage)
```

Notes:

- `Workflow` is the single place for configuration; every option after the name is keyword-only with a
  default, so resume and fork use the same settings as the first call.
- `Run` objects hold no cached state: `status`, `manifest` and `steps()` read the files on each call, so a
  `Run` never shows a stale status after another process changed the run.
- `fork(dry_run=True)` returns a `ForkPlan` and writes nothing; otherwise it returns the new `Run`.

## 3. A worked example

This workflow is also the main acceptance test (AC-1 in §7).

```text
import hone_flow as fk
from pydantic import BaseModel
from hone_flow.notifications import HttpWebhook

wf = fk.Workflow("song_video", storage=tmp_path / "flows", version="1",
                 notifications=[HttpWebhook(name="ops", url_env="OPS_WEBHOOK_URL",
                                            events=("run.completed", "run.awaiting_review"))])

class Timeline(BaseModel):
    duration: float
    lines: list[str]

@wf.global_step(version="1")
def style_guide(style: fk.Param[str]) -> dict:
    return {"style": style, "palette": ["black", "amber"]}

@wf.step(version="1")
def timeline(item: fk.Item, lyrics: fk.File) -> Timeline:
    lines = lyrics.path.read_text().splitlines()
    return Timeline(duration=len(lines) * 4.0, lines=lines)

@wf.step(version="1", resources="gpu:ollama", deterministic=False)
def shotlist(timeline: Timeline, style_guide: dict, ctx: fk.Context,
             review_note: str | None = None) -> dict:
    shots = [{"line": line, "look": style_guide["style"]} for line in timeline.lines]
    return {"shots": shots, "seed": ctx.seed, "note": review_note}

@wf.gate()
def review_shotlist(shotlist: dict) -> dict:
    return shotlist

@wf.step(version="1")
def render(review_shotlist: dict, ctx: fk.Context) -> fk.File:
    out = ctx.new_file("video.txt")
    out.write_text("\n".join(s["line"] for s in review_shotlist["shots"]))
    return fk.File(out)

items = [fk.Item("01", {"lyrics": fk.File("songs/01.md")}), fk.Item("02", {"lyrics": fk.File("songs/02.md")})]
run = wf.run(items, params={"style": "silhouette"})
assert run.status == "awaiting_review"            # stopped at the gate; the process may exit

run = wf.open_run(run.run_id)                     # later, any process that imports the same workflow
run.approve(step="review_shotlist", item="01")
run.reject(step="review_shotlist", item="02", note="darker lighting")
run.resume()                                      # 01 renders; 02's shotlist reruns with review_note, gate pauses again
assert run.status == "awaiting_review"
run.approve(step="review_shotlist", item="02"); run.resume()
assert run.status == "completed"
assert run.output("render", "01").path.name == "video.txt"

plan = run.fork(dry_run=True)                     # nothing changed: every row is "reuse"
new = run.fork(refresh=("shotlist",))             # new run id beside the source; shotlist + downstream rerun
assert new.manifest["fork_of"]["run_id"] == run.run_id
run.pin(); wf.cleanup(keep_last=1)
```

The run folder after the first call (local storage; S3 keys have the same shape):

```text
<storage>/song_video/
  runs/20260927T140311Z-3f9a1c/
    manifest.json   lease.json (only while a process holds the run)   spans.jsonl
    reports/        timing.json  output_sizes.json  summary.md
    style_guide/    metadata.json  inputs/  output/style_guide.json
    timeline/       item_01/ metadata.json  inputs/lyrics.md  output/timeline.json
                    item_02/ …
    shotlist/       item_01/ metadata.json  inputs/timeline.json  inputs/style_guide.json  output/shotlist.json
    review_shotlist/item_01/ metadata.json  inputs/shotlist.json  output/review_shotlist.json
  pinned_runs/
```

The same from the command line (`--flow module:attribute` imports the workflow; read and review commands
also accept `--storage URL --name NAME` instead):

```text
hone-flow run    --flow myflows:wf --items items.json --param style=silhouette [--until shotlist] [--items-filter 01,03]
hone-flow resume --flow myflows:wf RUN_ID [--until STEP] [--items 01,03]
hone-flow fork   --flow myflows:wf RUN_ID [--refresh shotlist] [--items 01] [--param style=noir] [--dry-run]
hone-flow status RUN_ID --flow myflows:wf            # or --storage s3://… --name song_video
hone-flow show   RUN_ID --step shotlist [--item 01]  # step record incl. attempts and reviews
hone-flow runs   [--updated-since 2026-09-01T00:00:00Z]
hone-flow approve RUN_ID --step review_shotlist --item 01
hone-flow edit    RUN_ID --step review_shotlist --item 01      # opens $EDITOR on the JSON output
hone-flow reject  RUN_ID --step review_shotlist --item 01 --note "darker lighting"
hone-flow pin RUN_ID;  hone-flow cleanup [--keep-last 20] [--older-than 30d] [--dry-run];  hone-flow notify-retry RUN_ID
```

Every command has `--json`. Exit codes: 0 success (also a run that stops at a gate or is partial),
1 failure (a failed run, or a library error printed as `error: …`), 2 usage error.

## 4. Concepts and rules

### 4.1 Steps and DAG inference

A step's parameters are resolved in this order: `fk.Context` (by annotation), `fk.Item` (by annotation),
`fk.Param[...]` (run-level params, by name), `review_note` and `previous` (by name, §4.8), another step's output (by the
step's name, or an output name from `outputs=`), a global step's output, an item input name.

Unresolvable names, cycles, duplicate names, a gate with no step input, an unknown `until` step and an
unsafe item id (empty, `.`, `..`, containing `/` or `\`) raise `WorkflowDefinitionError`, from
`wf.validate()` and at the start of every run, resume and fork. Item input names are only known once
items are given, so `wf.validate(items)` checks them (see decisions D-001).

A **global step** (`@wf.global_step`) runs once per run; item steps may take its output.
`outputs=("a", "b")` means the step returns a tuple and each element is its own output that other steps
name.

### 4.2 Storage layout and the `RunStorage` port

- `storage` is the workflow's storage root. Runs live at `<storage>/<name>/runs/<run_id>/`, pinned copies
  at `<storage>/<name>/pinned_runs/<run_id>/`. Workflows with the same name under different storage roots
  are separate namespaces. The storage root and the name are recorded in every manifest; opening a run
  always uses the storage and name it was created with, so a run never moves when the settings change.
- Run ids are `<UTC time %Y%m%dT%H%M%SZ>-<6 random hex>`: unique, and sortable by creation time.
- Inside a run: global steps at `<step>/`, item steps at `<step>/item_<item id>/`. Each step folder has
  `metadata.json`, `inputs/`, `output/` and, when there were earlier attempts, `attempts/<n>/output/`.
  Empty folders need no object on S3.
- `RunStorage` is one small Protocol, internal to hone-flow. Other tools read runs through the read API
  (§4.14) or the documented format (§4.15). Storage objects stand for the storage root; hone-flow's keys
  start with `<name>/` (D-030).

```python
from pathlib import Path
from typing import Protocol


class RunStorage(Protocol):
    url: str  # root URL / path, for manifests and messages

    def read_bytes(self, key: str) -> bytes: ...  # FileNotFoundError when missing

    def write_bytes(
        self, key: str, data: bytes, *, if_match: str | None = None, if_absent: bool = False
    ) -> None: ...  # WriteConflict when the condition fails

    def upload(self, local_path: Path, key: str) -> None: ...  # streamed file write

    def download(self, key: str, local_path: Path) -> None: ...  # streamed file read

    def copy(self, src_key: str, dst_key: str) -> None: ...  # server-side when possible

    def list(self, prefix: str) -> list[str]: ...  # keys (files) under a prefix, sorted

    def delete(self, prefix: str) -> None: ...  # a key or everything under a prefix

    def exists(self, key: str) -> bool: ...

    # optional (change 0008); hone_flow.storage.list_dir / file_size / read_range fall back to
    # list / read_bytes for storages without them
    def list_dir(self, prefix: str) -> list[Entry]: ...  # one level: Entry(name, is_dir, size)

    def size(self, key: str) -> int: ...

    def read_range(self, key: str, start: int, length: int) -> bytes: ...
```

- `if_match` is the **sha256 hex of the content the caller last read**; `if_absent=True` fails if the key
  exists. `write_bytes` is atomic for readers: no reader sees a half-written object.
- `LocalStorage(root)` (core): atomic writes (temporary file, `fsync`, `os.replace`); conditional writes
  are checked under an `fcntl` lock on the key's folder (D-031); `copy` makes a hardlink when source and
  target are on one filesystem, else a copy. Every stored file is read-only (`0444`), so a step writing to
  its input cannot change another run's file through a hardlink.
- `FsspecStorage(url)` (extra `s3`; any fsspec URL; `memory://` for tests): `copy` uses the filesystem's
  server-side copy (S3 CopyObject); `if_absent` uses exclusive create where the backend supports it (S3
  conditional create); `if_match` reads, compares and writes. **Limitation:** on backends without
  conditional writes this check is not atomic across machines; the run lease (§4.4) is what prevents two
  writers, and the check only catches mistakes.
- `MemoryStorage()` (`fk.testing`): a dict with exact semantics, for unit tests.
- `storage=` given as a string: a plain path or `file://` gives a `LocalStorage`; any other scheme gives
  an `FsspecStorage` (without the extra: `HoneFlowError` saying `pip install hone-flow[s3]`).
- Hashes are sha256 and only check integrity; they never name files.
- Steps always see local paths. With remote storage, inputs are downloaded into a local work folder
  (`$HONE_FLOW_WORKDIR`, default a temporary folder removed at the end of the call) and outputs are
  uploaded on commit.

### 4.3 Runs, statuses and the manifest

- `wf.run(items, params=…, seed=…)` creates a new run folder, writes `manifest.json` (identity, items,
  params, the step table with versions and source hashes, `seed`, `trace_id`), takes the run lease and
  executes (§4.6).
- **Step states** (per step and item, in the manifest's `state` table and in each `metadata.json`):
  `pending` (not reached yet, or reset by a rejection), `running` (manifest only), `done`, `failed`,
  `blocked` (an upstream step failed for this item), `skipped` (left out by `until`, `items_filter` or
  `items` of the call), `awaiting_review` (a gate waiting for a person), `interrupted` (was `running` when
  its process died), `not_selected` (a select step left the item out, §4.19; final, no folder). A step
  reused by a fork is `done`, with `reused_from` set and the label `reused`.
- **Run status** at the end of every call, first match wins: `failed` (any step `failed`, after the other
  items finished), `awaiting_review` (any gate waiting), `partial` (work left `skipped` or `pending` by the
  caller's selection), `completed`. While a call runs the status is `running`; a run whose process died
  while running is shown `interrupted` once its lease is taken over. Review decisions change step states,
  not the run status (D-038).
- The manifest's `state` table is a summary for fast listing; each step's `metadata.json` is the truth.
  When they disagree (a crash between the two writes) the metadata wins and resume repairs the manifest.
- Items and their inputs are recorded in the manifest: JSON values inline, `fk.File` / `fk.Dir` with the
  absolute original path, sha256 and size; every item input carries a `kind` (D-032). Item inputs are
  copied into each consuming step's `inputs/`.

### 4.4 Commit protocol and run lease

- **Run lease** `<run>/lease.json`: `{"owner": <uuid4>, "host", "pid", "acquired_at", "heartbeat_at",
  "expires_at"}`. Every call that changes a run (run, resume, a fork's new run, approve, edit, reject, pin,
  retry_notifications) takes it with `if_absent=True` and deletes it at the end. A running call refreshes
  `heartbeat_at` / `expires_at` every 10 s from a daemon thread (time to live: 60 s).
  - A lease is **live** when its host is this host and its pid is alive, or its host is another host and
    `expires_at` is in the future. A live lease held by someone else raises `RunLocked`, naming host, pid
    and expiry.
  - A **stale** lease (dead pid on this host, or expired on another host) is taken over with `if_match`
    on the lease content. Takeover marks the run's `running` steps `interrupted` and cleans up uncommitted
    step folders (below).
- **Step attempt commit.** A step attempt writes its output files first and its `metadata.json` last;
  `metadata.json` is the commit marker.
  1. The manifest marks the (step, item) `running` (conditional write).
  2. The step runs in a local work folder; its inputs were already written to `inputs/`.
  3. On success, outputs are written to `output/`, then `metadata.json` (`status: done`, or
     `awaiting_review` for a gate), then the manifest `state` (conditional write). On failure, whatever
     the step produced goes to `attempts/<n>/output/`, then `metadata.json` with `status: failed`, the
     error message and the traceback; `output/` keeps no failed result (D-036).
  4. **Retiring** a current result (before a rejection or an edit): copy `output/` to
     `attempts/<n>/output/`, write `metadata.json` with the attempt appended to `attempts` and no current
     output (`status: pending`), then delete `output/`.
- **Cleanup on takeover and resume:** a step folder without `metadata.json` is deleted; an
  `attempts/<n>/` folder not listed in `metadata.json` is deleted; `output/` of a step whose metadata has
  no current result is deleted. After this every step folder is exactly what its metadata says.
- **Manifest writes** are conditional: `if_match` with the sha256 of the manifest this process last read
  or wrote. A failed condition raises `WriteConflict` (a bug, or a second writer without the lease).

### 4.5 Resume

`run.resume(until=None, items=None, trace=None)` continues the **same run id**. It takes the lease
(taking over a stale one), cleans up, and runs every step that is `pending`, `failed`, `interrupted`,
`blocked` or `skipped` (restricted to `until` / `items` when given; outside them work stays `skipped`),
using the run's recorded params, items and seed. Retrying a failed step records attempt `n + 1`. Gates keep
waiting unless a person decided.

- **Resume never mixes versions.** Before running anything, resume compares the workflow in memory with
  the manifest, and raises `IncompatibleRun` (nothing is executed) when the workflow version differs, the
  step set or a step's inputs changed, or a step that still has work to do has a different `version`
  than the manifest records. The message names the steps and versions and says "fork the run instead:
  run.fork()". Done steps are never recomputed by resume, whatever their version.
- A step with remaining work whose **source hash** changed while its version did not: resume continues,
  logs a warning (logger `hone_flow`), appends `{"kind": "source_changed_version_unchanged", "step",
  "old", "new", "at"}` to the manifest's `warnings`, and adds a `warning` event with the same attributes
  to the `hone.flow.run` span.
- Item file inputs must still have the sha256 recorded at `wf.run`; a changed or missing file fails the
  step with a message that suggests a fork (D-036). Resume therefore never mixes inputs either.
- Resuming a pinned-only run (its `runs/` folder was cleaned up) or a detached `Run` raises
  `HoneFlowError` ("pinned runs are archives; fork it" / "resume needs the workflow: use wf.open_run").

### 4.6 Execution

- Default **breadth-first**: step A for all items, then step B; within a step, items in the given order.
  `order="depth_first"` runs each item through all its steps before the next item.
- **Batching:** a run of neighbouring steps (in topological order) with the same `gpu:` resources tag
  holds the injected `GpuLease` once for the batch, taken just before the first call of the batch, with
  the largest `vram_gb` of its steps (D-035); depth-first takes it per item.
- **Failure isolation:** an exception marks that (step, item) `failed` (traceback stored), its downstream
  steps for that item `blocked`, and the other items continue. `fail_fast=True` stops after the failure is
  committed and raises `StepFailed`. A failed global step blocks every item step that uses it.
- `ctx: fk.Context` gives `run_id`, `trace_id`, `item_id`, `step`, `attempt`, `seed`, `new_file(name)`,
  `new_dir(name)` (paths in the attempt's local work folder; returned `fk.File` / `fk.Dir` under it are
  stored as outputs), `logger`, `current_trace()`, `gpu_lease(...)`, `previous_output()` (§4.8) and
  `set_run_label(...)` (§4.16).
- **Seeds:** `ctx.seed = int(sha256(run seed, item, step)[:8], 16)`, stable across resume and retries. A
  step rerun because a fork asked for a refresh (`refresh_requested`), or because a person rejected its
  output, also mixes in the new run id or attempt number, so it produces a new sample.
- **Serializers:** Pydantic models (stored as JSON, with the model's import path in the metadata), JSON
  values (canonical JSON: sorted keys, UTF-8, 2-space indent), `fk.File`, `fk.Dir`, and types registered
  with `fk.register_serializer`. **File names:** a JSON or Pydantic output is `output/<output name>.json`;
  `fk.File` keeps the file's own name; `fk.Dir` keeps the folder's name and contents; a custom serializer
  writes `output/<output name>.<extension>`. `run.output()` loads the value back (the Pydantic model when
  its import path resolves, else a dict; `fk.File` / `fk.Dir` as local paths, downloaded into `local_dir`
  or a temporary folder for remote storage). An empty `fk.Dir` output fails the step, because an empty
  folder cannot be stored the same way on S3 (D-036).
- **Input snapshots:** before a step runs, each input is written to its `inputs/`: a step output or item
  file input as `inputs/<parameter name><suffix of the source file>` (`inputs/lyrics.md`,
  `inputs/timeline.json`), a folder as `inputs/<parameter name>/`, a JSON item value as
  `inputs/<parameter name>.json`. Params and the context are not files; params are in `metadata.json`. The
  step receives the snapshot (a local path under `inputs/`, or its download).
- `deterministic=False` is recorded (in `metadata.json` and as `hone.flow.deterministic`); every reused
  output is labelled `reused`, so a reused sample is never mistaken for a fresh one.

### 4.7 Fork

`run.fork(refresh=(), *, items=None, params=None, dry_run=False, trace=None)` creates a **new run id
beside the source** (`<storage>/<name>/runs/<new id>/`) from the source run and the **current** workflow
definition, then executes it like `wf.run`, stopping at gates and failures. The source is only read (a
live lease on it is fine: only committed `done` steps are reused).

- **The fork diff.** For every (step, item) of the new run hone-flow decides `reuse` or `run`, with a
  reason. The `run` reasons (joined with `; ` when several apply, `downstream_of` last) are:
  - `refresh_requested`: the step is named in `refresh`;
  - `version_changed 1->2`: the step version differs from the source step's metadata;
  - `source_changed`: the source hash differs, the version is equal;
  - `param_changed:<name>`: a `fk.Param` the step declares has a different value;
  - `input_changed:<name>`: the content hash of an external item input differs;
  - `workflow_version_changed 1->2`: applies to every step;
  - `new_step`, `new_item`: not in the source;
  - `not_done_in_source`: the source step is not `done` (failed, pending, skipped, blocked, awaiting
    review, interrupted);
  - `items_changed`: a final or select step whose fork has other items than the source (§4.18);
  - `downstream_of:<step>`: an upstream step of the same item, or a global step, will run (for a final
    or select step: of any item). It names the
    nearest upstream step that runs for a reason of its own (D-039).
- Everything else is `reuse` with reason `unchanged`: its `output/` and `inputs/` are **copied** into the
  new run (server-side copy on S3; hardlink or copy locally), and its `metadata.json` is written anew with
  `status: done`, the label `reused`, `reused_from: <source run id>`, `source_attempt: <n>` and an empty
  `attempts` list (the source's attempt folders are not copied). A reused gate keeps its decision (its
  `approved` / `edited` label and its reviews are copied).
- `refresh=("lyrics",)` refreshes `lyrics` and everything downstream of it; `refresh={"lyrics": ["02"]}`
  refreshes it for those items only (change [0004](changes/0004-steps-over-all-items.md)). `fork()` with no `refresh`
  refreshes exactly the changed steps and their downstream: incremental reruns happen inside a run's
  family tree, never from unrelated runs.
- `params` are merged over the source's params (secret-stripped values are compared, §4.13). `items`:
  `None` means the source's items; otherwise a list of item ids (keeping the source's definition) and/or
  `fk.Item` objects (a known id replaces that item's inputs; a new id is added, all its steps
  `new_item`). A fork with `items` contains only those items. External file inputs are re-hashed at their
  recorded original path; a file that no longer exists there counts as unchanged and the source's
  snapshot is used.
- `dry_run=True` returns a `ForkPlan(source_run_id, rows)` with rows `ForkPlanRow(item, step, action,
  reason)` (`item=None` for global steps) and writes nothing. The executed fork records the same plan in
  its manifest (`fork_of: {"run_id", "plan"}`) and follows it exactly.
- Seeds: the fork keeps the source's run seed, so reruns are reproducible, except for
  `refresh_requested` steps (§4.6).
- A step-level rerun ("rerun this step for these items with these params") is a fork:
  `wf.open_run(run_id).fork(refresh=(step,), items=items, params=params)`.

### 4.8 Gates and reject-and-revise

- A gate's function runs (usually the identity) and its (step, item) becomes `awaiting_review`; the call
  ends normally with run status `awaiting_review` and the process may exit. Downstream steps stay
  `pending`. The gate **reviews the output of the steps it takes as input** (its *producers*).
- `run.approve(step=<gate>, item, note="", actor=None)`: the gate becomes `done` with the label
  `approved`; `resume()` continues downstream.
- `run.edit(step=<gate>, item, value=…)`: the gate's current output is retired to `attempts/<n>/` (§4.4),
  `value` is stored as the gate's output (validated against the gate's return type when that is a
  Pydantic model), label `edited`, status `done`; `resume()` continues downstream with the edited value.
- `run.reject(step=<gate>, item, note=…)`: saves the note, retires the gate's output and each producer's
  output to their `attempts/` (attempt status `rejected`, with the note), sets the gate and its producers
  `pending`, and sets every step downstream of the producers for that item `pending` (retiring their
  outputs too, attempt status `replaced`). On `resume()` each producer reruns as a new attempt and
  receives the latest note through a parameter named `review_note: str | None = None` if it declares one
  (`None` when never rejected), and its latest rejected (or, downstream, replaced) output through a
  parameter named `previous` (`None` on a first attempt and in a fork; change
  [0006](changes/0006-previous-output-on-revise.md); also `ctx.previous_output()`); then the gate pauses
  again. A global producer reruns once for the run, and its downstream steps become `pending` for every
  item. The gate is retired first, so a crash part way
  never lets an unreviewed output pass the gate (D-038).
- Every decision is appended to the gate's `metadata.json` `reviews` list (`decision`, `actor` = `actor`
  or `$USER`, `actor_kind` = `automated` when the call passed `automated=True` else `person` (change
  [0010](changes/0010-seed-and-actor-kind-in-records.md)), `note`, `at`, `attempt`) and recorded as a `hone.flow.gate` span in the run's
  `spans.jsonl`, in the run's trace. A decision on a step that is not a gate, or not `awaiting_review`,
  raises `ReviewError`. Decisions take the run lease (§4.4).

### 4.9 Notifications (`hone_flow.notifications`)

- Destinations are small frozen dataclasses with the same fields: `SlackWebhook`, `MattermostWebhook`,
  `DiscordWebhook`, `HttpWebhook` (`name: str`, `url_env: str`, `events: tuple[str, ...] =
  ("run.completed", "run.failed")`, `browse_url: str | None = None`, a template with `{run_id}` and
  `{workflow}`). One delivery function and a table of payload formatters (Slack and Mattermost share one
  format). HTTP goes through the standard library (`urllib.request`, 10 s timeout); no extra dependency.
- **Events:** `run.completed`, `run.failed`, `run.awaiting_review`, emitted at the end of a run, resume or
  fork call whose final status is that one, to every destination subscribed to it.
- **Delivery states:** the run status and a `pending` event (stable id `<run_id>/<event>/<seq>`) are
  committed to the manifest's `notifications` list **before** any delivery; after each attempt the
  destination's entry records `delivered` or `failed` with the error, attempt count and time. A delivery
  failure never changes the run status and never raises from `run`, `resume` or `fork`.
- `run.retry_notifications()` (CLI `notify-retry`) retries every `pending` and `failed` delivery once and
  returns the `Delivery` results. There is no background retry. Delivery is **at least once**: a crash
  after the server accepted a message but before `delivered` was written sends it again; the event id is
  in every payload (and in the `Idempotency-Key` header of `HttpWebhook`) for deduplication.
- **Message:** workflow, run id, event, a short summary (`2 items completed · 0 failed`; the first error
  line for `run.failed`; the waiting gates for `run.awaiting_review`), the run location, and the
  `browse_url` when set. Never outputs, inputs or params.
- **Secrets:** a webhook URL is read from `os.environ[url_env]` at delivery time only; it never appears in
  manifests, metadata, reports, spans, logs, CLI output or error messages (errors say
  `<url from $ENV_NAME>`). A missing variable is a `failed` delivery with the message "environment
  variable OPS_WEBHOOK_URL is not set". The manifest records each destination's kind, `url_env` and
  `browse_url`, so a detached run can retry without the workflow's code (D-042).

### 4.10 Measurements and reports

- `measure=` names what is collected: `"timing"` (wall time per step attempt, GPU lease wait),
  `"output_sizes"` (bytes per output file and per step), `"cpu"`, `"memory"`, `"disk"` (psutil, extra
  `metrics`), `"gpu"` (NVML, extra `gpu`). Default `("timing", "output_sizes")`; `()` for none. An unknown
  name raises `WorkflowDefinitionError`; a name whose extra is missing raises `HoneFlowError` at
  construction ("pip install hone-flow[metrics]"). With `"gpu"` but no GPU visible, the GPU keys are left
  out and `summary.md` says so.
- System measurements come from a sampler thread (every second, plus one sample at the end of the step):
  summary attributes on the step span, `metrics.sample` events, and a summary in the step's
  `metadata.json` `measurements`. Units follow OpenTelemetry (D-020).
- Reports are (re)written at the end of every call: `reports/timing.json`, `reports/output_sizes.json`,
  `reports/system_resources.json` (only for the enabled measurements) and always `reports/summary.md`
  (status, item counts per state, slowest steps, largest outputs, resource peaks, warnings).

### 4.11 Pinning and cleanup

- `run.pin()` requires a finished run (status `completed` or `failed`, no live lease; otherwise
  `HoneFlowError`). It copies every file of `runs/<id>/` to `pinned_runs/<id>/`, verifies every copied
  file's sha256 against the source, then writes `pinned: true, pinned_at` into both manifests (the pinned
  manifest last, so a half-made copy is invisible). A pinned copy is an immutable archive: resume and
  reviews on it raise, fork works, and a second pin is refused (D-040).
- Listings show **one logical run** per run id: `pinned=True` when a pinned copy exists; the `runs/` copy
  is used while it exists. `open_run(id)` opens `runs/<id>` if it exists, else `pinned_runs/<id>`, else
  raises `RunNotFound`.
- `cleanup(keep_last=None, older_than=None, dry_run=False)` deletes run folders under `runs/` only. A run is
  deleted when it is outside the newest `keep_last` runs (by `created_at`) **and** older than `older_than`
  (a `timedelta`; CLI `30d`, `12h`), each condition applying when given; with neither, `HoneFlowError`.
  Never deleted: anything under `pinned_runs/`, and a run with a live lease. Returns
  `CleanupReport(deleted, kept, locked)`; `dry_run=True` returns the same report and deletes nothing.

### 4.12 Records (`<run>/spans.jsonl`)

- Spans: `hone.flow.run` (one per run, resume or fork call), `hone.flow.step` (one per (step, item) the call
  touched, including `skipped`, `blocked`, `awaiting_review`, `pending`, `interrupted` and reused ones),
  and `hone.flow.gate` (for gate executions and for every review decision; D-033). Span attributes use
  OpenTelemetry names where they exist and `hone.flow.*` otherwise (`hone.flow.status`,
  `hone.flow.attempt`, `hone.flow.seed`, `hone.flow.reused_from`, `hone.flow.fork_of`, …); the full list is in
  [`docs/records.md`](../docs/records.md).
- Spans are written to `<run>/spans.jsonl`, one span JSON object per line, large values inline. Local
  storage appends; remote storage rewrites the object after each step commit and at the end of the call
  (the lease guarantees one writer; D-034). A crash loses at most the spans of the steps that were
  running. Spans also go to `sink=` when given. The content-capture switch (`HONE_CAPTURE_CONTENT=0`)
  applies to spans.
- **Trace context:** a run's `trace_id` is chosen at `wf.run` (from `trace=` or the active context, else
  new) and recorded in the manifest; resume continues that trace, with a new `hone.flow.run` span per
  call. A fork joins the caller's trace like `wf.run`, else starts its own, with `hone.flow.fork_of` on its
  run span and a span link to the source run. Steps run with the context set, and `ctx.current_trace()`
  passes it to the libraries a step calls, so their spans join the step's trace. Keys of the caller's
  trace context other than `traceparent` are copied onto every span of the call and into each step's
  context.

### 4.13 Secrets

Secret-looking values (`sk-…`, `Bearer …`, the values of environment variables named in notification
destinations) are replaced by `***` in manifests, metadata, tracebacks, reports, spans and CLI output.
Fork diffs compare stripped param values. Recorded params and review notes are stripped, so resume, fork
and a revising producer see `***` (D-037, D-040): pass secrets through the environment, not params.

### 4.14 Public read API

For tools that read runs without the workflow's code (analysis tools, a future web UI):
`fk.open_runs(storage, name)` returns a `RunHistory`. `runs(updated_since=…)` returns
`RunSummary(run_id, workflow, workflow_version, status, created_at, updated_at, fork_of, pinned, items,
location, label, description, attempts)` for every logical run whose manifest `updated_at` is at or after the ISO-8601 time given, newest
first; it reads every manifest. For large histories (change
[0008](changes/0008-read-api-for-browsers.md)) `run_ids()` lists the ids newest first from one-level
listings of `runs/` and `pinned_runs/` without reading a manifest, and `summaries(run_ids)` reads only
those manifests (in parallel on remote storage), leaving out ids without one. `Run.lease()` returns
`LeaseInfo(host, pid, acquired_at, heartbeat_at, expires_at, live)` or `None`, so a browser tells a
running run from one whose process died. `open_run(run_id)` returns a detached `Run`: `manifest`, `steps()`, `output()`, `spans()`,
`approve` / `edit` / `reject`, `pin` and `retry_notifications`. `steps()` returns `StepRecord(step, item,
kind, status, version, source_hash, attempt, attempts, labels, reused_from, reviews, review_note, params,
inputs, outputs, error, started_at, ended_at, duration_ms, measurements, location)`, from `metadata.json`
when it exists, else from the manifest state (a `pending`, `skipped` or `blocked` step has no folder).
Readers ignore unknown keys; a `format_version` they do not know raises `HoneFlowError` with
"upgrade hone-flow".

### 4.15 Run format (`format_version: "1"`)

The files below are a public, versioned format: other tools may read them without importing hone-flow.
Adding optional keys keeps version `"1"`; removing a key or changing its meaning needs `"2"`. Pydantic
models in `run_format.py` define them; [`docs/run-format.md`](../docs/run-format.md) shows each with an
example and a test checks it against the models.

`manifest.json`:

```json
{"format_version": "1", "run_id": "20260927T140311Z-3f9a1c", "workflow": "song_video",
 "workflow_version": "1", "storage": "s3://hone-flow/projects/oneshotstudio",
 "location": "s3://hone-flow/projects/oneshotstudio/song_video/runs/20260927T140311Z-3f9a1c/",
 "hone_flow_version": "0.1.0", "created_at": "…", "updated_at": "…", "status": "awaiting_review",
 "seed": 0, "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", "params": {"style": "silhouette"},
 "items": [{"id": "01", "inputs": {"lyrics": {"kind": "file", "path": "/home/me/songs/01.md",
            "sha256": "…", "size": 812}}}],
 "steps": [{"name": "shotlist", "kind": "step", "version": "1", "source_hash": "…",
            "resources": "gpu:ollama", "deterministic": false, "inputs": ["timeline", "style_guide"],
            "outputs": ["shotlist"]}],
 "state": {"style_guide": "done", "timeline/01": "done", "shotlist/01": "done",
           "review_shotlist/01": "awaiting_review"},
 "measure": ["timing", "output_sizes"], "warnings": [],
 "fork_of": null, "pinned": false, "pinned_at": null,
 "notifications": [{"id": "20260927T140311Z-3f9a1c/run.awaiting_review/1", "event": "run.awaiting_review",
                    "created_at": "…", "summary": "1 item awaiting review at review_shotlist",
                    "deliveries": {"ops": {"state": "delivered", "attempts": 1, "error": null, "at": "…"}}}],
 "calls": [{"kind": "run", "started_at": "…", "ended_at": "…", "host": "…", "pid": 4242,
            "until": null, "items": null}],
 "label": "Rain on a tin roof", "description": "first draft", "attempts": {"shotlist/01": 2}}
```

`<step>[/item_<id>]/metadata.json`:

```json
{"format_version": "1", "run_id": "…", "step": "shotlist", "item": "01", "kind": "step",
 "status": "done", "version": "1", "source_hash": "…", "deterministic": false, "resources": "gpu:ollama",
 "params": {}, "seed": 1234567, "attempt": 2,
 "inputs": {"timeline": {"from": "step:timeline", "files": {"timeline.json": {"sha256": "…", "size": 311}}}},
 "outputs": {"shotlist": {"type": "json", "files": {"shotlist.json": {"sha256": "…", "size": 1234}}}},
 "labels": [], "reused_from": null, "source_attempt": null, "review_note": "darker lighting",
 "reviews": [], "error": null,
 "attempts": [{"attempt": 1, "status": "rejected", "started_at": "…", "ended_at": "…",
               "note": "darker lighting", "error": null,
               "outputs": {"shotlist": {"type": "json", "files": {"shotlist.json": {"sha256": "…", "size": 1201}}}}}],
 "started_at": "…", "ended_at": "…", "duration_ms": 5120,
 "measurements": {"timing": {"duration_ms": 5120, "lease_wait_ms": 3}, "output_sizes": {"total_bytes": 1234}},
 "span_id": "00f067aa0ba902b7"}
```

- Output `type`: `json`, `pydantic:<module>:<qualname>`, `file`, `dir`, `custom:<serializer name>`.
- Input `from`: `step:<name>`, `global:<name>`, `item_input`, `item_value`.
- The top-level fields describe the latest attempt; `attempts` lists earlier attempts (`failed`,
  `rejected`, `replaced`). A failed latest attempt has `status: failed` and `error: {"message",
  "traceback"}`, and its files are under `attempts/<attempt>/output/`.
- `lease.json` (§4.4) and `spans.jsonl` (§4.12) complete the format; `reports/*` are for people and may
  change between versions.

### 4.16 Run labels (change [0009](changes/0009-run-labels.md))

A run may carry a human `label` and `description` (optional manifest keys; `null` or missing when
unset). `wf.run(label=, description=)` and `run.fork(label=, description=)` set them (a fork defaults to
`"<source label> (fork)"` and the source's description); `ctx.set_run_label(label, description=None)`
sets them from a step, applied with that step's committed result (a failed attempt changes nothing);
`run.set_label(label, description=None)` changes them later under the run lease (attached or detached,
not on a pinned archive). `description=None` keeps the current description; `None` or blank text removes
a value. They appear in `RunSummary`, the `hone.flow.run` span (`hone.flow.run.label`), notification
messages (`label`, only when set) and the CLI (`run --label`, `runs`, `label RUN_ID TEXT`). Labels are
secret-stripped and nothing depends on them.

### 4.17 Progress while a run runs (change [0003](changes/0003-progress-while-a-run-runs.md))

Every call logs at `INFO` on the `hone_flow` logger: the run's start with its id and location, each step
start and end (status, time), each GPU lease wait and grant, and the run's end. `run`, `resume` and `fork`
take `on_event=`, a callable that receives the same events as small dicts in order: `run_started`
(`kind`, `location`, sent right after the manifest records the call, before any step), `step_started`
(`step`, `item`, `attempt`), `step_finished` (also `status`, `duration_ms`), `lease_waiting` (`name`,
`vram_gb`), `lease_granted` (`name`, `wait_ms`), `run_finished` (`status`); every event also has
`event`, `run_id`, `workflow` and `at`. Units that do not run (reused, skipped, blocked) send no event.
A callback that raises is logged as a warning and ignored (`progress.py`).

### 4.18 Final steps: steps over all items (change [0004](changes/0004-steps-over-all-items.md))

- `@wf.final_step()` registers a step of kind `final` that runs once per run (`item` `None`, folder
  `<step>/`, like a global step) after the items. A parameter resolved to an item step's output (by step
  or `outputs=` name) has the argument kind `items`: it receives `dict[item_id, output]` of the items
  where that step is `done`, in manifest order; each is snapshotted as
  `inputs/<parameter>/<item id>/<file>` with `from: "items:<step>"`. Global and final steps' outputs
  arrive as single values (`from: "final:<step>"` for a final step). A final step cannot take item inputs
  or `fk.Item`; item steps cannot take a final step's output; global steps cannot take either
  (`WorkflowDefinitionError`).
- Decision (`schedule.py`): run-level inputs first (failed / blocked → `blocked`, not done → `pending`);
  then, unless `partial_ok=True`, any needed item `failed` / `blocked` → `blocked`, any `skipped` (left out
  by the call's selection) → `skipped`, anything else not `done` → `pending`.
- Order: breadth-first already puts it after its item steps (topological order). Depth-first walks
  *phases*: each item through the steps before the next fan-in step, then that step, then on.
- Fork: a final step is downstream of every item of its item inputs, so refreshing one item's step
  reruns it; a fork whose item ids differ from the source's gives it `items_changed`. `refresh` may map
  step names to item ids (`{"script": ["02"]}`; CLI `--refresh script=02`).
- Reject: a final step downstream of a rejected producer is retired (`replaced`); item steps downstream
  of a run-level step that is sent back are retired for every item.

### 4.19 Select steps: choosing which items continue (change [0007](changes/0007-select-items-mid-run.md))

- `@wf.select_step()` registers a fan-in step of kind `select`: it takes item steps' outputs as dicts
  like a final step (§4.18, same decision and `partial_ok`), and returns the ids that continue: a list of
  item ids or `fk.Selection(keep, reasons)`. It is stored as `fk.Selection` (a Pydantic output) at
  `<step>/`; an id that is not an item of the run fails the step.
- An item step that takes the select step's output (it receives the `fk.Selection`) runs only for kept
  items. For another item its state becomes `not_selected`, and so does every item step downstream of a
  `not_selected` one for that item. A failed or blocked upstream wins (`blocked`). `not_selected` is
  terminal (not work left): the run can be `completed`; final steps see only selected items (only `done`
  units are passed). The reasons live in the selection's output; `not_selected` units have no folder.
- `resume(items=[...])` naming an item with a `not_selected` step raises `HoneFlowError` (fork instead).
- Fork: a unit that was `not_selected` in the source, with no reason of its own to run and nothing
  upstream running, is planned `reuse` with reason `not_selected` and stays `not_selected`. When the
  select step reruns, the steps that take its output rerun for every kept item.
- Reject: a select step downstream of a rejected producer is retired like a final step, and its
  `not_selected` units become `pending`, so the selection is made again.

### 4.20 Items made by a step (change [0005](changes/0005-items-from-a-step.md))

- A `@wf.global_step` annotated `-> fk.Items` is a *producer*; `@wf.step(per="<producer>")` (and
  `@wf.gate(per=...)`) runs once per item it produced. `wf.validate()` raises `WorkflowDefinitionError`
  when `per=` names anything else, or when an item step takes an item step over other items.
- The producer's output is stored as `output/<step>.json` (serializer `custom:items`; `run.output` gives
  `fk.Items`). Before it commits, its items are checked: ids new in the run and safe, JSON inputs only,
  every input a `per=` step needs present; a problem fails the producer. On commit they are appended to
  the manifest's `items` with `items_from: "<producer>"` (optional key; the step table records `per`),
  with `pending` units for its `per=` steps. The run's own items have `items_from: null`.
- Order: `per=` counts as a dependency for the order of steps (the producer runs first; breadth-first
  batches all produced items per step) and for `until=`, but not for the fork diff. Depth-first runs a
  phase's run-level steps first, then walks items, including items added meanwhile. A fan-in step over
  produced items also waits for the producer.
- When a producer commits again (a rerun in the same run or in a fork), an item with the same id and
  inputs keeps its units; an item whose inputs changed has its units retired (`replaced`) and set
  `pending`; a new item gets `pending` units; an item no longer produced loses its units and folders.
  If anything changed, run-level steps downstream of the `per=` steps start over. In a fork these
  decisions update `fork_of.plan` (`produced_item_changed`, `new_item`, `downstream_of:<producer>`), so a
  dry run shows only the rows known before the producer runs.
- Selections (`items_filter`, `resume(items=)`) name produced items like any other once they exist.

## 5. Modules

| Module | Responsibility |
|---|---|
| `workflow.py` | `Workflow`, decorators, validation, `run` / `open_run` / `runs` / `cleanup` |
| `dag.py` | DAG inference, topological order, downstream sets |
| `types.py` | `Item`, `File`, `Dir`, `Param`, `Context`, `RunSummary`, `StepRecord`, `ForkPlan(Row)`, `CleanupReport`, `Delivery` |
| `serialize.py` | serializers, canonical JSON, output file names, sha256 |
| `invoke.py` | calling a step function with its resolved arguments; seeds; failure text |
| `inputs.py` | input snapshots |
| `storage.py` | `LocalStorage`, `open_storage(storage)` |
| `fsspec_storage.py` | `FsspecStorage` (imports fsspec lazily) |
| `run_format.py` | Pydantic models for manifest, metadata and lease; `format_version` checks |
| `run_lease.py` | acquire, heartbeat thread, takeover, release |
| `commit.py` | attempt commit, retire, cleanup of uncommitted folders |
| `executor.py` | order, batching, GPU lease, failure isolation, calling commit |
| `resume.py` | what resume checks first: versions (`IncompatibleRun`), source-change warnings, not-selected items |
| `produced.py` | items made by a step: checks, adding them to the manifest, re-production |
| `schedule.py` | the units of a step, depth-first phases, the decision per unit (run, keep, a state) |
| `run.py` | `Run`: status, steps, output, spans, resume, fork, reviews, pin |
| `fork.py` | the fork diff, `ForkPlan`, copies of reused steps |
| `reviews.py` | approve / edit / reject-and-revise |
| `history.py` | `RunHistory`, listings, `updated_since`, pin, cleanup |
| `notifications.py` | destination dataclasses, the formatter table, delivery, retry |
| `metrics.py` | the sampler thread (psutil / NVML) |
| `reports.py` | `reports/*.json`, `summary.md` |
| `progress.py` | progress events: `INFO` log lines and the `on_event` callback |
| `leases.py` | `FileLockGpuLease`, `NullGpuLease`, entry-point loading |
| `spans.py` | building flow spans, writing `spans.jsonl` |
| `_records.py`, `_tracing.py` | span sinks, trace context |
| `errors.py` | typed errors |
| `ports.py` | `GpuLease`, `RecordSink`, `RunStorage`, `TraceContext`, `PORTS_VERSION` |
| `adapters/hone_models.py` | resolves a GPU lease from the `hone.gpu_leases` entry point lazily |
| `testing/` | `MemoryStorage`, `FakeGpuLease`, `WebhookServer`, contract checkers |
| `cli.py` | the Typer CLI (extra `cli`) |

Dependencies: the core needs only the standard library and pydantic. Extras: `cli` (typer), `s3`
(fsspec and s3fs), `metrics` (psutil), `gpu` (nvidia-ml-py).

## 6. Examples

`examples/` holds one runnable file per concept, indexed in reading order in
[`examples/README.md`](../examples/README.md). Each opens with a docstring that explains **what** it shows,
**how** (the calls, in order) and **why** (the problem it solves), then runs top to bottom with fakes and a
temporary folder (no network, no GPU), prints a few lines and asserts the key facts. The test suite runs
every example and checks that it has that docstring and is listed in the index.

## 7. Guarantees (acceptance cases)

Each case below has a test in `tests/e2e/` named `test_ac<N>_*` (AC-27 is `test_examples.py` and
`test_docs.py`; AC-28 and AC-29 are in `tests/gpu/` and need real models).

| AC | Scenario | Expected |
|---|---|---|
| AC-1 | The §3 example end to end (temporary folders, `fk.testing.WebhookServer`) | stops at the gate; approve 01 + reject 02 + resume: 01 renders, 02's shotlist reruns with the note, the gate pauses; approve + resume → `completed`; a dry-run fork is all `reuse`; `fork(refresh)` gives a new id; pin + cleanup |
| AC-2 | Run folder format | the layout of §3 / §4.2 exactly; `manifest.json` and every `metadata.json` validate against format v1; `inputs/` files equal (sha256) what each step received; output names `<step>.json` and the user's file / folder names; run ids unique and sortable; a copy of the folder elsewhere is readable with `fk.open_runs` |
| AC-3 | Serializers and outputs | Pydantic, JSON, `fk.File`, `fk.Dir`, a custom serializer (`.ext`), `outputs=("a","b")` round-trip through `run.output`; `inputs/<param><suffix>` names |
| AC-4 | Params, context, seeds | only declared params reach a step; `ctx.seed` is stable across resume and retry, different per item and step; `deterministic=False` is recorded |
| AC-5 | Failure and resume | a step raises for item 02: item 01 completes, 02 is `failed` with a traceback, its downstream `blocked`, the run `failed`; after fixing the code (same version) `resume()` runs only 02's failed and blocked steps, attempt 2 is recorded, attempt 1 is in `attempts`; `fail_fast=True` raises `StepFailed` after committing |
| AC-6 | Crash and lease | a subprocess SIGKILLed during step 2 of 3 leaves `lease.json`; `resume()` in a new process takes over (dead pid), the running step becomes `interrupted` then reruns, the uncommitted folder is removed, committed steps are not rerun, no partial file is in any `output/`; a live lease → `RunLocked`; a lease from another host with an expired heartbeat is taken over, an unexpired one is not |
| AC-7 | Incompatible resume | bumping the version of a step with remaining work → `IncompatibleRun` naming step and versions and suggesting `fork()`, nothing executed; a workflow version change or step-set change → `IncompatibleRun`; a done step's version bump → resume proceeds; a source change without a version bump → a warning logged, in `manifest.warnings` and as a `warning` span event |
| AC-8 | Partial runs | `until="shotlist"` runs shotlist and upstream only; `items_filter=["01"]` runs item 01 only; the rest is `skipped` and the run `partial`; `resume(items=["02"])` runs 02 only; `resume()` completes |
| AC-9 | Fork with refresh | `fork(refresh=("shotlist",))`: a new run id under `runs/`; shotlist and downstream rerun for all items, timeline and the global step copied (`reused`, `reused_from`, identical sha256); the source untouched; deleting the source folder leaves the fork fully readable (copies, not references); gates downstream pause again |
| AC-10 | Automatic fork diff | `fork()` with: a bumped `shotlist` version (`version_changed 1->2`, render `downstream_of:shotlist`, timeline reused); a changed param (`param_changed:style` only for steps declaring it); item 01's lyrics file edited (`input_changed:lyrics` for item 01's timeline and downstream, item 02 reused); a source edit without a version bump (`source_changed`); a workflow version change (all `workflow_version_changed`); no change (all `reuse`); a failed source step (`not_done_in_source`); the executed fork calls exactly the `run` rows |
| AC-11 | Dry-run fork plan | `fork(dry_run=True)` returns the plan with the rows and reasons of AC-10, `refresh_requested` for named steps; the storage listing is identical before and after; the executed fork's `manifest.fork_of.plan` equals the dry-run plan |
| AC-12 | Fork as a step rerun | `open_run(id).fork(refresh=("shotlist",), items=["02"], params={"style": "noir"})` returns a run with only item 02, shotlist and downstream rerun with `style="noir"`; an `fk.Item` with a known id replaces its inputs, a new id is `new_item` |
| AC-13 | Gates: approve and edit | approve 01 → downstream runs on resume, label `approved`; edit 02 with a new value → the old gate output in `attempts/1/`, label `edited`, downstream receives the edited value; decisions in `reviews` and as `hone.flow.gate` spans with the actor; approve on a non-gate or a gate not awaiting review → `ReviewError`; a decision while another process holds the lease → `RunLocked` |
| AC-14 | Reject and revise | `reject(note=…)`: the note is saved; gate and producer outputs move to `attempts/` (status `rejected`); the producer and its downstream are `pending`; resume reruns the producer with `review_note` = the note (a producer without the parameter reruns too), with a new seed, then the gate pauses; a second reject passes the latest note; approve + resume completes |
| AC-15 | Attempts | after the AC-5 and AC-14 flows, `run.steps()` shows every attempt with status, times, error / traceback or note; only the current result is in `output/`; attempt folders not listed in the metadata are removed on resume |
| AC-16 | Pin and cleanup | pinning an `awaiting_review` run → `HoneFlowError`; pinning a completed run → `pinned_runs/<id>` with every file hash verified, both manifests `pinned: true`; `wf.runs()` lists one logical run with `pinned=True`; `cleanup(keep_last=1)` deletes older `runs/` folders including the pinned one's original, never `pinned_runs/`, never a run with a live lease; `dry_run=True` deletes nothing and reports the same; `open_run` of the cleaned pinned run reads the archive, its `resume()` raises, its `fork()` works; `cleanup()` with no option raises |
| AC-17 | Notifications | `HttpWebhook` and `SlackWebhook` against `WebhookServer`: `run.completed` / `run.awaiting_review` delivered only to subscribed destinations; the payload has workflow, run id, event, summary, location, the formatted `browse_url` and the event id (+ `Idempotency-Key`); the server's handler reads the manifest during the request and sees the event `pending`; afterwards `delivered` with the attempt count |
| AC-18 | Notification outage, retry, secrets | a server returning 500 and an unreachable port: the run status stays `completed`, the delivery is `failed` with the error; `retry_notifications()` after recovery → `delivered`, attempts 2; a missing environment variable → `failed` with the variable named; a planted token in the webhook URL appears in no file under the run folder, no span, no log record and no CLI output |
| AC-19 | Measurements and reports | the default `measure` → `timing` + `output_sizes` in the metadata and in `reports/timing.json`, `reports/output_sizes.json`, `summary.md`; `measure=()` → only `summary.md`; `("cpu", "memory", "disk")` with psutil → `system_resources.json`, span summary attributes, at least one `metrics.sample` event for a 2 s step; an unknown name → `WorkflowDefinitionError` |
| AC-20 | Spans and trace linking | `spans.jsonl` holds `hone.flow.run` / `.step` / `.gate` spans; statuses include `skipped`, `blocked`, `awaiting_review`; `hone.flow.attempt` on retried steps; `hone.flow.reused_from` on reused steps and `hone.flow.fork_of` on the fork's run span; no cache attributes; a nested span made with `ctx.current_trace()` shares the trace id and parent; a caller's `trace=` is joined; resume continues the trace id; `sink=MemorySink()` receives the same spans; `run.spans()` reads them back; the capture switch is honoured |
| AC-21 | Read API | `fk.open_runs(storage, name)` without importing the workflow: `runs(updated_since=t)` returns only runs updated at or after `t`; `steps()` includes `pending`, `skipped`, `blocked`, `awaiting_review`, `failed`, attempts, reviews, `reused_from`; `output()` returns the Pydantic model / `fk.File`; detached `resume()` / `fork()` raise `HoneFlowError`; notification states are visible in `manifest`; an unknown `format_version` raises |
| AC-22 | S3 via fsspec | the §3 flow (run, gate, resume, fork, pin, cleanup, read API) on `storage="memory://bucket/proj"`; `check_run_storage` passes for `LocalStorage`, `MemoryStorage` and `FsspecStorage("memory://…")`; an integration test repeats run + fork + pin against a local S3 server (moto) |
| AC-23 | GPU batching and order | breadth-first with a `gpu:` tag and `FakeGpuLease`: the lease is entered once per batch, not per item; the call order is asserted; the `order="depth_first"` order is asserted; `Workflow(gpu="file_lock")` resolves the entry point |
| AC-24 | CLI | `run`, `resume`, `fork` (`--dry-run`), `status`, `show`, `runs --updated-since`, `approve`, `edit` (`EDITOR` = a script), `reject`, `pin`, `cleanup --dry-run`, `notify-retry`: outputs, `--json` schemas, exit codes 0 / 1 / 2, `--storage/--name` without `--flow` for read and review commands |
| AC-25 | Definition errors | an unresolvable parameter, a cycle, a duplicate name, a gate without a step input, an unsafe item id, an unknown `until` step, an unsafe workflow name → `WorkflowDefinitionError` with a clear message |
| AC-26 | Secrets | a planted `sk-…` in params and in a step's exception message is `***` in the manifest, metadata, tracebacks, reports, spans and CLI output |
| AC-27 | Examples and README | every `examples/*.py` runs and has the What / How / Why docstring; `examples/README.md` lists every file; every Python block in README and `docs/` runs; the README quickstart runs from the built wheel with no extras |
| AC-28 (real model) | A step calling Ollama through plain `httpx`, `resources="gpu:ollama"`, `FileLockGpuLease` | runs, commits, resumes after a planted failure, `fork(refresh=…)` gives a new sample, reused steps are labelled `reused`; the model is unloaded afterwards |
| AC-29 (real model, optional) | A step rendering a small image through ComfyUI | an `fk.File` output with its own name in `output/`; skipped with a reason if ComfyUI is not available |
| AC-30 | Progress | `on_event` receives `run_started` (run id, location; the manifest exists) before any step, then lease, step and `run_finished` events in order with statuses and attempts; `resume` / `fork` too (`kind`); a raising callback is logged and the run completes; the `INFO` log lines name the run, location, steps and lease waits |
| AC-31 | Final steps | breadth- and depth-first: the final step runs after every item and receives dicts by item id; snapshots under `inputs/<param>/<item>/`, `from: items:<step>`, kind `final`; `blocked` after an item fails, `skipped` for a partial call then run by `resume()`; `partial_ok=True` uses the done items; `fork(refresh={"script": ["02"]})` reruns item 02 and the final steps and reuses the rest (dry-run plan = executed plan; CLI `--refresh script=02`); other items → `items_changed`; rejecting a producer replaces the final step; definition errors |
| AC-32 | Produced items | breadth- and depth-first with no own items: the producer's `fk.Items` become manifest items (`items_from`), `per=` steps run per produced item (GPU steps batched), a final step gets them all; a failed producer leaves no items and blocks the final step; `until=` runs the producer, `resume(items=[<produced id>])`; a fork with changed params reruns only changed and new chapters and the final step (plan `produced_item_changed`, `new_item`), drops chapters no longer produced; bad producers (id clash, file input, missing input, a plain list) fail; definition errors for `per=` |
| AC-33 | Previous output on revise | a producer declaring `previous` gets `None` first, then the latest rejected output (its Pydantic type) after each rejection, `ctx.previous_output()` the same; several outputs as a tuple; a step retired as `replaced` gets its replaced output; a fork gets `None` |
| AC-34 | Select steps | breadth- and depth-first: the select step runs after every item, keeps ids by rule; the others' downstream steps are `not_selected` (no folder, spans with that status), the run `completed`, a final step sees only kept items; `resume(items=[<not selected>])` raises; `partial_ok` chooses among done items while a failed item stays `blocked`; a plain list is stored as `fk.Selection`, an unknown id fails the step; an unchanged fork keeps `not_selected` (plan `reuse`/`not_selected`), fewer items → `items_changed`; a rejection upstream resets `not_selected` |
| AC-35 | Read API for browsers | `run_ids()` reads no manifest and lists `runs/` and `pinned_runs/` ids newest first; `summaries(ids)` keeps the order, leaves out unknown ids, has `pinned`, `label` and `attempts` (`{"render/b": 2}` after a retry, also in the manifest); `list_dir`, `file_size`, `read_range` on local, memory and fsspec storage and through the fallbacks; `check_run_storage` covers them; `lease()` is live during a step, `None` after, not live for an expired lease of another host |
| AC-36 | Run labels | `wf.run(label=)`; `ctx.set_run_label` applied on commit and not by a failed attempt; `run.set_label` detached, keeping the description, `RunLocked` while running, refused on a pinned archive; fork default `"<label> (fork)"`; secrets stripped; `RunSummary`, run span, HTTP payload and CLI `label` / `runs` show it |
| AC-37 | Seeds and who decided | `hone.flow.seed` on the run span (run seed) and step spans (`ctx.seed`); `approve(..., automated=True)` records `actor_kind: automated` in `reviews` and on the decision span, the default is `person` (also for reviews written before the key existed); CLI `--automated` |

## 8. The problems this design prevents

The design was shaped by the failures of two hand-written pipeline runners in a real project (a local
song-to-video pipeline). Each maps to a rule above:

| Problem | Rule |
|---|---|
| `state.json` "done" flags that ignore changed inputs | the fork diff reruns what depends on a changed input (`input_changed:<name>`) |
| no way to see what a run used and produced without reading code | self-contained run folders with input snapshots |
| step lists duplicated in three files, drifting apart | one DAG from the code; `IncompatibleRun` instead of silently mixing versions |
| `--from` / `--until` / `--force` rewritten in each runner | `until`, `items`, `fork(refresh=…)` |
| a review note that nothing reads | `review_note` reaches the producing step |
| a crash that forces a full rerun | per-step commits and lease takeover |
| a finished overnight run nobody noticed | notifications |
| stage batching by hand (language model, then image model, then video) | `gpu:` batches |

`examples/video_pipeline.py` reproduces that pipeline's shape with fake steps.

## 9. Known limitations (0.1.0)

- `runs()` and `cleanup` read every manifest (after one-level listings): fine for hundreds of runs, slow
  for many thousands on S3 (no index, by design); a run table uses `run_ids()` and `summaries()` per page.
- On storage without conditional writes the `if_match` check is not atomic across machines; the run lease
  is what keeps one writer per run. S3 uses conditional create for leases.
- `spans.jsonl` on remote storage is rewritten after each step commit (quadratic in the number of steps of
  one call); local storage appends.
- A secret-looking param or review note is stored as `***`, so resume, fork and the producer see `***`:
  pass secrets through the environment.
- `FileLockGpuLease` is POSIX only and ignores `vram_gb`; batches are runs of neighbouring steps in
  topological order.
- An item whose input file is gone can still be forked (the source's snapshot is used), but resuming the
  run needs the original file.
- The optional ComfyUI real-model case (AC-29) has not yet run against a real ComfyUI workflow; it skips
  when ComfyUI is not available.
