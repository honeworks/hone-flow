# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Why the design changed is recorded in
[design/changes/](design/changes/).

## [0.1.0] - unreleased

### Added (from the demo apps, change records 0003-0009)
- Run labels ([0009](design/changes/0009-run-labels.md)): `wf.run(label=, description=)`,
  `run.fork(label=, description=)`, `run.set_label(...)`, `ctx.set_run_label(...)`; optional manifest
  keys `label` / `description`, `RunSummary.label` / `.description`, the `hone.flow.run.label` span
  attribute, `label` in notification messages, `hone-flow run --label`, `hone-flow label RUN_ID TEXT`.
- Steps over all items ([0004](design/changes/0004-steps-over-all-items.md)): `@wf.final_step()`
  (kind `final`, `partial_ok=`) receives `dict[item_id, output]` of item steps; `run.fork(refresh=
  {"step": ["02"]})` and `hone-flow fork --refresh step=02` refresh a step for some items; the fork
  reason `items_changed`; depth-first runs walk phases split at fan-in steps.
- Items from a step's output ([0005](design/changes/0005-items-from-a-step.md)): a global step
  annotated `-> fk.Items` produces items; `@wf.step(per="<step>")` / `@wf.gate(per=...)` run per
  produced item; optional manifest keys `items[].items_from` and `steps[].per`; the fork reason
  `produced_item_changed`.
- Choosing which items continue mid-run ([0007](design/changes/0007-select-items-mid-run.md)):
  `@wf.select_step()` returns the ids that continue (`fk.Selection(keep, reasons)`); the step state
  `not_selected` (terminal, counts as done); `resume(items=[...])` refuses not-selected items.
- A revised producer receives its previous output
  ([0006](design/changes/0006-previous-output-on-revise.md)): a parameter named `previous` (and
  `ctx.previous_output()`) gives the step's latest rejected or replaced output.
- Seeds and who decided, in the records ([0010](design/changes/0010-seed-and-actor-kind-in-records.md)):
  `hone.flow.seed` on run and step spans; `approve` / `edit` / `reject(..., automated=True)` (CLI
  `--automated`) record `reviews[].actor_kind` and `hone.flow.gate.actor_kind`.
- Read API for browsers ([0008](design/changes/0008-read-api-for-browsers.md)):
  `RunHistory.run_ids()` (no manifest read) and `RunHistory.summaries(run_ids)`, `Run.lease()` →
  `fk.LeaseInfo`, optional storage methods `list_dir` / `size` / `read_range` (`fk.Entry`) on every
  built-in storage with fallbacks in `hone_flow.storage`, and the optional manifest key `attempts`
  (also `RunSummary.attempts`). `runs()` and `cleanup` no longer list every file recursively.
- Progress while a run runs ([0003](design/changes/0003-progress-while-a-run-runs.md)): `INFO` log
  lines on the `hone_flow` logger and `on_event=` on `wf.run`, `run.resume` and `run.fork`
  (`run_started` with the run id and location, step, lease and `run_finished` events).

First release. Design: [0002 run folders](design/changes/0002-run-folders.md), which supersedes the
unpublished [0001 initial design](design/changes/0001-initial-design.md).

### Fixed (concurrent writers)
- `SqliteSpanSink` sets `busy_timeout` before switching to WAL and retries the switch briefly: when several
  processes created the same fresh store at once, the switch ignored the timeout and a writer dropped its
  spans ("database is locked").

### Added (design history)
- `design/`: why hone-flow exists, the design today (`design/current.md`), one record per design change
  (`design/changes/`), the implementation decisions and the early history; `CONTRIBUTING.md`.

The first 0.1.0 build (content-addressed asset store, SQLite metadata, automatic cross-run cache) was
never published and is replaced by the run-folder design. Why: design change
[0002 (run folders)](design/changes/0002-run-folders.md), which supersedes
[0001 (the first design)](design/changes/0001-initial-design.md); the design today is
[design/current.md](design/current.md). The entries below the redesign section describe that first build;
git history keeps its code.

### Added (run-folder redesign)
- **Run folders** (`<storage>/<name>/runs/<run_id>/`): `manifest.json`, one folder per step (and item)
  with `inputs/` snapshots (copies), `output/` with readable names (`<step>.json`, the file's own name
  for `fk.File` / `fk.Dir`, `<output>.<extension>` for registered serializers) and `metadata.json`
  written last as the commit marker; `spans.jsonl`; `reports/`. The format is versioned
  (`format_version: "1"`), modelled in `run_format.py` and documented in `docs/run-format.md`.
- `fk.Workflow(name, *, storage, version, measure, notifications, order, fail_fast, gpu, sink)`;
  `wf.run(items, params=, until=, items_filter=, seed=, trace=)` returns a `fk.Run`.
- `RunStorage` port with `fk.LocalStorage` (atomic writes, folder-lock conditional writes, read-only
  hardlinked files), `fk.FsspecStorage` (extra `s3`: S3 / any fsspec URL, exclusive create on S3) and
  `fk.testing.MemoryStorage`; `contracts.check_run_storage`.
- The run lease (`lease.json`: owner, host, pid, heartbeat every 10 s, 60 s TTL), takeover of stale
  leases, `fk.RunLocked`; recovery after a crash (`interrupted` steps, uncommitted folders removed).
- `run.resume(until=, items=, trace=)`: pending, failed, interrupted, blocked and skipped work, as new
  attempts; `fk.IncompatibleRun` when step versions would mix; source-change warnings.
- `run.fork(refresh=, items=, params=, dry_run=, trace=)` with the fork diff (`fk.ForkPlan`,
  `fk.ForkPlanRow`: `refresh_requested`, `version_changed`, `source_changed`, `param_changed`,
  `input_changed`, `workflow_version_changed`, `new_step`, `new_item`, `not_done_in_source`,
  `downstream_of`); reused results are copied and labeled `reused` with `reused_from`.
- Gates: `run.approve` / `run.edit` / `run.reject` (reject-and-revise: the producer reruns with
  `review_note`), attempts kept under `attempts/<n>/`, decisions in `reviews` and `hone.flow.gate` spans.
- Read API: `fk.open_runs(storage, name)` → `fk.RunHistory` (`runs(updated_since=)`, `open_run`,
  `cleanup`), `fk.RunSummary`, `fk.StepRecord`, detached runs; `run.pin()` (verified copy to
  `pinned_runs/`) and `cleanup(keep_last=, older_than=, dry_run=)` → `fk.CleanupReport`.
- Measurements (`measure=`: timing, output_sizes, cpu, memory, disk, gpu) and `reports/`.
- Notifications (`hone_flow.notifications`: `SlackWebhook`, `MattermostWebhook`, `DiscordWebhook`,
  `HttpWebhook`): pending events committed before delivery, delivery states, `run.retry_notifications()`
  → `fk.Delivery`; `fk.testing.WebhookServer`; webhook URLs are never recorded.
- The `hone-flow` CLI: `run`, `resume`, `fork` (`--dry-run`), `status`, `show`, `runs`, `approve`,
  `edit`, `reject`, `pin`, `cleanup`, `notify-retry`, with `--json`, `--storage/--name` and exit codes.
- Twenty-one explained examples (`examples/`), the README and `docs/` rewritten, real-model tests
  (AC-28 Ollama, AC-29 ComfyUI).

### Changed (run-folder redesign)
- Package description: "Run AI workflows of plain-function steps as self-contained run folders, with
  resume, fork, partial runs and human gates."
- `fk.Workflow(name, *, version="1", gpu=None)`: `version` is keyword-only; the workflow name must be a
  safe folder name (`[A-Za-z0-9_.-]+`, not `.` / `..`), else `WorkflowDefinitionError`.
- `@wf.global_step` takes `outputs=` like `@wf.step`.
- The wheel smoke test runs `examples/quickstart.py` (the README quickstart).

### Removed (run-folder redesign)
- `LocalAssetStore`, `FsspecAssetStore`, `SqliteMetaStore` (`stores/`), the `AssetStore` port,
  `fk.testing.MemoryAssetStore` / `MemoryMetaStore`, `contracts.check_asset_store`.
- Cache keys, miss reasons and `wf.plan` (`cache.py`, `plan.py`); `cache=`, `cache_expiration`,
  `strict_versions`; `fk.Select`, `fk.Plan`, `fk.PlanRow`, `fk.RunResult`, `fk.RunStatus`,
  `fk.StepResult`.
- The old `wf.run` / `wf.status` / `wf.output`, `wf.rerun` (use `run.fork(refresh=...)`),
  `wf.approve` / `edit` / `reject` (now on `Run`), `wf.export` (the run folder is the export), `wf.gc`
  (now `cleanup`), `wf.pin` / `unpin` (now `run.pin()`), asset hooks (`wf.on_asset_committed` /
  `on_asset_deleted`, `fk.AssetEvent`), `Workflow(store=, meta=, strict_versions=, metrics=,
  metrics_interval=)` and the old executor.
- `fk.GateBlocked`, `fk.AssetNotFound`.
- The CLI commands `plan`, `export` and `gc`.
- The old acceptance tests, the old docs pages and the `respx` dev dependency.

### Changed (working with the other honeworks packages, first build)
- A cached / reused pair whose step source changed without a version bump records the reason
  `cache_hit; source_changed_version_unchanged` (or `reused_from_run:<id>; ...`), so the warning reaches
  the span store, where hone-lens's `cache_health` detector reads it.

### Added (first build)
- `Workflow` with `@wf.step`, `@wf.gate`, `@wf.global_step`; DAG inferred from parameter names;
  `WorkflowDefinitionError` for unknown names, cycles and duplicates.
- Canonical JSON / Pydantic / `File` / `Dir` assets, `register_serializer`.
- `LocalAssetStore` (content-addressed, crash-safe writes) and `SqliteMetaStore` (WAL).
- Cache keys with explained miss reasons, `wf.plan`, breadth-/depth-first executor, failure isolation.
- `fk.Select` (`only`, `from_`, `until`, `items`, `force`, `refresh`), `cache=False`, source-change
  warnings with `strict_versions`, `reused` labels for non-deterministic steps, `wf.rerun`.
- Gates: `wf.approve` / `wf.edit` / `wf.reject` with decisions and labels; rejection notes reach the
  gated step as `review_note`. Asset hooks `wf.on_asset_committed` / `wf.on_asset_deleted`
  (`fk.AssetEvent`). `wf.export(run_id, dir)`.
- Crash safety: runs and steps of killed processes are marked `interrupted` and resumed; scratch
  folders of killed runs are removed; Ctrl-C marks the running step `interrupted`.
- Records: `hone.flow.run` / `hone.flow.step` / `hone.flow.gate` spans (the shared honeworks span format) in the caller's
  trace (`wf.run(trace=...)`, `wf.rerun(..., trace=...)`, `fk.current_trace()`, `ctx.current_trace()`);
  `SqliteSpanSink` (default), `JsonlSpanSink`, `MemorySink`, `NullSink`; secrets stripped; content capture
  switch (`HONE_CAPTURE_CONTENT=0`).
- System metrics per step (psutil via extra `metrics`, NVML via extra `gpu`): summary attributes and
  `metrics.sample` events; `Workflow(metrics=False, metrics_interval=...)`.
- GPU leases: batches of neighbouring `gpu:` steps hold one lease; `FileLockGpuLease`, `NullGpuLease`,
  `Workflow(gpu="<entry point>")` through `hone.gpu_leases`; contract checkers `check_record_sink`.
- `FsspecAssetStore(url, cache_dir=..., storage_options=...)` (extra `s3`): S3 / any fsspec filesystem
  with a hash-verified local read cache.
- `wf.gc(dry_run=...)`, `wf.pin` / `wf.unpin`; `on_asset_deleted` hooks fire on gc.
- `hone-flow` CLI (extra `cli`): `plan`, `run`, `status`, `show`, `approve`, `edit` ($EDITOR),
  `reject`, `export`, `gc`, `runs`, `pin`, with `--json` and exit codes.
- `GateBlocked` for `only` selections below an unapproved gate; `refresh` gives seeded steps a new seed.
- Docs (README, guides, CLI and metadata-schema reference) and `examples/video_pipeline.py`, all executed
  by tests; real-model tests for Ollama (AC-19), ComfyUI (AC-20, optional) and GPU metrics.

### Fixed (first build)
- Skipped gates (rerun, `from_` / `only`) no longer bypass reviews; edits survive a gate rerunning;
  strict mode keeps unchanged cache entries; `plan` shows `blocked_by_gate` behind a gate that will run.
