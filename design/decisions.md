# Implementation decisions

Small choices made while building hone-flow, where the design left a question open. Larger changes have
their own record in [`changes/`](changes/). Numbers are kept from the original log, so gaps are entries
that belong to a change record:

- D-005 to D-007, D-009 to D-017, D-019, D-023, D-025 to D-027 applied only to the first design and are
  summarised in [0001](changes/0001-initial-design.md#implementation-choices-of-the-first-build).
- D-028 was the decision to rebuild around run folders: that is [0002](changes/0002-run-folders.md).

Entries flagged for the owner's review are listed first, under
[Awaiting owner review](#awaiting-owner-review).

## Awaiting owner review

These choices were made while building and are waiting for the owner's review. When one is reviewed, a
line saying what was decided is added here.

| Item | The question | Status |
|---|---|---|
| [D-030](#d-030-runstorage-keys-are-relative-to-the-storage-root) | `RunStorage` keys are relative to the storage root, where the design said the workflow root. Accept? | awaiting owner review |
| [D-037](#d-037-secrets-in-params-and-item-values) | Secrets in params are stored stripped, so resume and fork pass `***`. Accept, or refuse secret-looking params at `wf.run`? | awaiting owner review |
| [D-039](#d-039-how-the-fork-diff-names-downstream_of-and-how-reused-steps-are-copied) | `downstream_of` names the nearest upstream step that runs for its own reason. Accept? | awaiting owner review |
| [D-040](#d-040-pinning-cleanup-and-listings) | `pin()` refuses a second pin (pinned copies never change). Accept? | awaiting owner review |
| Design choices in [0002](changes/0002-run-folders.md#decision) | `order` and `fail_fast` on `Workflow`; the `partial` run status; a rejection resets every step downstream of the producers. Accept? | awaiting owner review |
| AC-29 (real ComfyUI step) | The optional ComfyUI acceptance case has only ever been skipped (ComfyUI not available where the tests ran). Run it against a real ComfyUI workflow, or drop it? | awaiting owner review |
| [0010](changes/0010-seed-and-actor-kind-in-records.md) | Seeds on spans and `automated=` gate decisions, added at hone-lens' request and implemented straight away (additive). Accept? | awaiting owner review |
| Simplifications in [0007](changes/0007-select-items-mid-run.md#implementation-notes) and [0005](changes/0005-items-from-a-step.md#implementation-notes) | Select reasons live in the selection output only; `resume(items=)` of a not-selected item has no `force=`; a rerun selection reruns every kept item's downstream in a fork; produced items take JSON inputs only. Accept? | awaiting owner review |
| D-011, D-023, D-027 (first design) | No `MetaStore` Protocol; the gc keep policy; content capture not applied to the metadata tables. See [0001](changes/0001-initial-design.md#implementation-choices-of-the-first-build). | awaiting owner review; the code they concern was removed by 0002, so no action is needed unless the owner wants the history amended |

## Workflows and steps

### D-001: Item inputs are checked when items are given

The design asks for unresolvable parameters to be reported "at definition time", but item input names
are only known once items are given. Any parameter name that is not a step output, `Param`, `Context` or
`Item` is treated as an item input: `wf.validate()` catches cycles, duplicate names and bad global steps,
and `wf.validate(items)` (called by every run, resume and fork) catches unknown names, listing the step
outputs and the item inputs. This keeps steps plain functions with no extra declarations. Resolution
order: Context / Item / Param (by annotation), `review_note`, any step output (including a global
step's), then item inputs, so a step output shadows an item input of the same name.

### D-002: `fk.Param[T]` is `Annotated[T, marker]`

`Param` is a generic alias of `typing.Annotated`, so type checkers see the plain `T` inside the step and
hone-flow finds the marker at runtime.

### D-003: Decorator options are keyword-only; `outputs=` names tuple outputs

`@wf.step(version="1", *, resources=..., ...)`. `outputs=("a", "b")` means the step returns a tuple; each
element is stored as its own output and other steps depend on it by name. A step without `outputs` has
one output named after the step. `vram_gb` is a decorator option. Custom serializers take an `extension`
for readable output file names.

### D-004: Exception names

`StepFailed` keeps its name as designed (ruff's N818 rule is disabled for `errors.py`).

### D-008: Where the run options live

`order` and `fail_fast` are `Workflow` options (so resume and fork use them too), `seed` is a `wf.run`
option, and outputs are read with `run.output(step, item)`.

### D-018: Item ids must be safe folder names

Item ids are folder names (`item_<id>`), so empty ids, `.`, `..`, and ids containing `/` or `\` raise
`WorkflowDefinitionError`.

## Storage and run folders

### D-029: What the rebuild kept of modules built on the old runner

The design kept `invoke.py`, `spans.py` and the CLI skeleton, but most of their code used the first
design's runner and stores. Only what works on plain values was kept: `invoke.py` (`step_seed`, with the
same numbers as the first build; `split_outputs`; the secret-stripped failure text), `spans.py` (the
span status and `exception` event rules), and the CLI's Typer app, `load_flow`, `load_items` and
`parse_params`. The old executor was deleted and rewritten. `Workflow` options arrived with the features
that use them, so no parameter was ever unused.

### D-030: `RunStorage` keys are relative to the storage root

The design said keys are relative to the workflow root `<storage>/<name>/`, and `storage=` may be a
`RunStorage` object. Is such an object the storage root or the workflow root? Storage objects stand for
the storage root and hone-flow's keys start with `<name>/`. So `storage="s3://b/p"` and
`storage=FsspecStorage("s3://b/p")` mean the same thing, and one storage object can hold several
workflows, like a URL can. The manifest records `storage` as the storage's `url`.

### D-031: Local conditional writes lock the key's folder; every stored file is read-only

A lock file per key (`<key>.lock`) would appear in run folders and pinned copies. `LocalStorage` takes the
`fcntl` lock on the key's folder instead, so no extra files appear. Temporary files of atomic writes are
named `<name>.tmp-<hex>` and never listed. Every file `LocalStorage` stores is `0444`, which is what makes
hardlinked copies safe; `upload` always copies (it never links a user's file, whose permissions must not
change).

### D-032: Item inputs in the manifest always carry a `kind`

An inline JSON object could look like a file record, so every item input is an object with `kind`:
`json` (with `value`), `file` or `dir` (with `path`, `sha256`, `size`). Readers never guess.

### D-034: `spans.jsonl` is appended locally and rewritten elsewhere

Local storage appends and fsyncs (`LocalStorage.append`, a method outside the port); other storages
rewrite the whole object after each step commit. A local `spans.jsonl` shared with a pinned copy through
a hardlink is copied before appending, so a pinned archive never changes. Rewriting locally too would be
simpler, but a run of thousands of steps would rewrite megabytes after every step.

### D-036: Failed attempts, empty folders, changed item inputs

- A failed attempt's files (from `ctx.new_file` / `new_dir`) are stored under `attempts/<n>/output/`;
  files of a half-stored result under `output/` are deleted, so `output/` only ever holds a committed
  success.
- An empty `fk.Dir` output fails the step with a clear message: an empty folder has no object on S3, so it
  could not be stored the same way everywhere.
- Item file and folder inputs are snapshotted when a step runs and must still have the sha256 recorded at
  `wf.run`; a changed or missing file fails the step ("changed since the run started ... fork the run").
  Resume therefore never mixes inputs; a fork re-hashes and reruns what depends on a changed input.
- An `item: fk.Item` parameter receives the item with every input snapshotted into the step's `inputs/`,
  so the step never reads the user's original paths.

### D-041: fsspec details and CLI errors

`FsspecStorage` turns off fsspec's listing cache (other processes change run folders, and a cached listing
could hide a lease). `if_absent` uses exclusive create (S3 conditional write `If-None-Match`, `memory://`)
and falls back to check-then-write on backends without it. The CLI prints every failure as one
`error: ...` line with exit code 1 (library errors, storage errors, a failing `$EDITOR`, a broken user
module); usage errors exit 2. CLI output is secret-stripped.

## Execution, records and measurements

### D-020: psutil is an extra; OpenTelemetry units

CPU, memory and disk measurements need the `metrics` extra (the core stays stdlib + pydantic); GPU
measurements need the `gpu` extra and a driver. Measurements are chosen with `measure=`. Utilisations are
OpenTelemetry ratios 0 to 1 (system-wide CPU; mean over GPUs); memory is in MB (`system.memory.usage.peak_mb`
for the system, `hone.flow.process.memory.peak_mb` for this process); disk bytes are counted from the step
start. A sample is taken every second and once when the step ends, so every executed step has at least one
`metrics.sample` event, with the same keys as the summary.

### D-021: GPU leases: the file lock and entry points

`FileLockGpuLease(path=None)` locks `$HONE_GPU_LEASE_LOCK` or `<tmp>/hone-gpu-lease.lock`, deliberately not
the lock that `scripts/gpu-lock.sh` holds around real-model test runs (`$HONE_GPU_LOCK`), or a run under
that script would wait for itself. One exclusive lock whatever `vram_gb` says; reentrant per thread; POSIX
only. `Workflow(gpu="<name>")` loads a `hone.gpu_leases` entry point (`file_lock` is built in); a
registered class is instantiated without arguments. `ctx.gpu_lease()` defaults to the step's `gpu:` tag,
so it re-enters the batch lease. A lease that cannot be taken fails the call with `HoneFlowError`.

### D-022: Secrets are stripped from everything recorded

Params, tracebacks and errors go through the same secret stripping (`sk-...`, `Bearer ...` → `***`) in
manifests, metadata, reports, spans and notifications, not only in spans.

### D-033: One span per touched step; gates' spans are `hone.flow.gate`

One span per (step, item) a call touches: `hone.flow.gate` for a gate, `hone.flow.step` otherwise, with
the same attributes. Review decisions add their own `hone.flow.gate` span with
`hone.flow.gate.decision` / `actor` / `note`; gate executions carry no decision attribute. Steps a call
leaves alone (done earlier, still awaiting review) get no span; steps it marks `skipped`, `blocked`,
`pending` or `interrupted` get one. No duplicate spans for one execution, and the decision attribute only
ever means a decision.

### D-035: A GPU batch asks for the largest `vram_gb` of its steps

The steps of a batch may declare different amounts; the batch's lease asks for the largest. A step can
still take a nested `ctx.gpu_lease(...)`.

## Gates, fork, pinning and notifications

### D-037: Secrets in params and item values

`wf.run` passes the caller's params to its steps; the manifest and metadata record stripped values; resume
and fork pass the recorded (stripped) values, so a secret in a param reaches steps only in the first call.
Hence "pass secrets through the environment, not params". Item JSON values are data, not settings: the
manifest keeps them as given (steps read them back from it); secret-looking strings are still stripped from
errors, tracebacks, reports and spans. Listed under [awaiting owner review](#awaiting-owner-review).

### D-038: Review decisions and the run status; what a rejection's reset is called

- Approve, edit and reject change step states, not the run status: the status is set at the end of a run,
  resume or fork call, so a run whose gates were all approved stays `awaiting_review` until `resume()`
  continues it (recomputing it would show `partial`, which is misleading). Notifications are sent by calls,
  not decisions.
- Attempt statuses: `rejected` for the gate and its producers; `replaced` for an edited gate output and for
  every other output a rejection resets.
- Inside `reject`, the gate is retired first, then the producers and their downstream, so a crash part way
  never lets an unreviewed output pass the gate.
- The actor is `actor=` or `$USER` (falling back to `getpass.getuser()`).

### D-039: How the fork diff names `downstream_of`, and how reused steps are copied

- `downstream_of:<step>` names the nearest upstream step that runs for a reason of its own; a step that
  only runs because of its upstream passes that step's cause on. With `shotlist -> review_shotlist ->
  render`, a shotlist version bump gives render `downstream_of:shotlist`, while a gate that never finished
  gives render `downstream_of:review_shotlist`. Several reasons are joined with `; `, `downstream_of` last.
- Reused steps: `inputs/` and `output/` are copied; `metadata.json` is written anew with `status: done`,
  labels `reused` (plus a gate's `approved` / `edited`), `reused_from`, `source_attempt`, `attempt: 1` and
  no attempts. Their spans carry `hone.flow.reused_from` and keep the source's timings (the span says when
  the result was made).
- A fork joins the caller's trace (`trace=` or the active context) like `wf.run`, else starts a new one;
  its run span links to the source run's first call span.

Listed under [awaiting owner review](#awaiting-owner-review).

### D-040: Pinning, cleanup and listings

- `run.pin()` refuses a run that already has a pinned copy (`HoneFlowError: already pinned`) and refuses
  on the pinned copy itself: pinned copies never change. To archive a later state, fork and pin the fork.
- Every file except `lease.json` and `manifest.json` is copied and its sha256 checked; the `runs/` manifest
  gets `pinned: true`; the pinned `manifest.json` is written last. Until then the copy is invisible to
  listings and `open_run`; a failed copy is deleted.
- `RunSummary.pinned` means "a pinned copy exists"; the `runs/` copy is the one listed while it exists.
- Cleanup deletes each run under its run lease; a run whose lease is live is reported as `locked`.
- Review notes are secret-stripped before they are stored, like params (D-037), so a producer receives a
  stripped `review_note` too.

Listed under [awaiting owner review](#awaiting-owner-review).

### D-042: The notification delivery function

A delivery needs the run (workflow, run id, location) and the destination as recorded in the manifest, so
a detached run can retry without the workflow's code. `notifications.deliver(manifest, event, delivery)`
sends one event to one recorded destination and returns the error or `None`; `deliver_event(folder,
manifest, event)` loops over an event's destinations and records each result; `retry(folder)` does it for
every pending and failed delivery. Each delivery entry in the manifest stores the destination's `kind`,
`url_env` and `browse_url`, never the URL.

## Command line

### D-024: CLI style

`--json` on every command; exit codes 0 (success, also a run stopped at a gate or partial), 1 (a failed run
or a library error) and 2 (usage error). `edit` runs `$EDITOR <file>.json` (arguments split with `shlex`);
an unchanged file records nothing, and file / folder outputs are refused. Items files are a JSON list of
`{"id", "inputs"}` with `{"$file": path}` / `{"$dir": path}` relative to the file; `--param name=value`
parses JSON values, else strings. Output is plain `typer.echo` text, so the `cli` extra needs only `typer`.
Read and review commands work with `--storage/--name` instead of importing the workflow.
