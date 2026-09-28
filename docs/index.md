# hone-flow documentation

hone-flow runs multi-step workflows whose steps are plain Python functions and keeps every run as a
self-contained folder. Start with the [README](../README.md) quickstart, then read [concepts](concepts.md).

| Page | What it covers |
|---|---|
| [concepts.md](concepts.md) | steps, items, params, the context, how the DAG is inferred, global steps, gates, step and run statuses, seeds, determinism labels |
| [run-folders.md](run-folders.md) | the folder layout, the commit protocol (`metadata.json` last), the run lease and takeover, input snapshots, output names, attempts |
| [run-format.md](run-format.md) | reference of `manifest.json`, `metadata.json`, `lease.json` and `spans.jsonl` (`format_version: "1"`) |
| [resume-and-fork.md](resume-and-fork.md) | resume rules, `IncompatibleRun`, partial runs, fork with `refresh`, the fork diff and its reasons, dry runs, step reruns |
| [across-items.md](across-items.md) | final steps over all items (fan-in), items made by a step, choosing which items continue |
| [gates.md](gates.md) | approve, edit, reject-and-revise, `review_note`, where decisions are recorded, reviews without the workflow's code |
| [notifications.md](notifications.md) | Slack / Mattermost / Discord / HTTP webhooks, events, delivery states, retries, secrets, testing with `WebhookServer` |
| [measurements.md](measurements.md) | `measure=`, what each step records, `reports/` |
| [storage.md](storage.md) | `storage=` forms, `LocalStorage`, `FsspecStorage` (S3), `MemoryStorage`, the work folder, pinning and cleanup |
| [records.md](records.md) | `spans.jsonl`, span names and `hone.flow.*` attributes, trace context, `sink=`, content capture, secrets |
| [cli.md](cli.md) | every `hone-flow` command and option, `--json`, exit codes, the items file |
| [adapters.md](adapters.md) | the ports (`GpuLease`, `RecordSink`, `RunStorage`), fakes, contract checkers, the `hone.gpu_leases` entry point |
| [read-api.md](read-api.md) | `fk.open_runs`, `RunHistory`, `RunSummary`, `StepRecord`, detached runs: what tools like hone-lens build on |

The [examples](../examples/README.md) are small runnable scripts, one per concept; each page links the
ones that show its subject. Every Python block in these pages is run by the test suite
(`tests/e2e/test_docs.py`), so the code you see works with the current version.
