# hone-flow examples

One runnable file per concept. Each opens with a docstring saying **what** it shows, **how** (the calls,
in order) and **why** (the problem it solves), then runs top to bottom with fakes and a temporary folder
(no network, no GPU), prints a few lines and asserts the key facts. `tests/e2e/test_examples.py` runs
every file here, so they always work with the current code. Start at the top; each builds on the ones
before it.

```bash
uv run python examples/quickstart.py
```

The last column names the section of [the design](../design/current.md) each example illustrates.

| # | File | Concept | In one sentence | Design |
|---|---|---|---|---|
| 1 | [`quickstart.py`](quickstart.py) | steps, items, DAG, `wf.run`, `run.output` | Two plain functions become a workflow wired by parameter names; every run is a folder. | §2, §4.1 |
| 2 | [`run_folder.py`](run_folder.py) | the run folder | Walk `manifest.json`, `metadata.json`, `inputs/` snapshots and readable `output/` files with plain `json`. | §4.2, §4.15 |
| 3 | [`params_and_context.py`](params_and_context.py) | `fk.Param`, `fk.Context`, files, `outputs=` | Run settings, stable seeds, a work folder for files and folders, and steps with several outputs. | §4.1, §4.6 |
| 4 | [`global_steps.py`](global_steps.py) | `@wf.global_step` | Work done once per run and shared by every item. | §4.1 |
| 5 | [`serializers.py`](serializers.py) | Pydantic, JSON, `fk.register_serializer` | How outputs are stored, named (`<output>.<extension>`) and loaded back. | §4.6 |
| 6 | [`failure_and_resume.py`](failure_and_resume.py) | failures, `blocked`, `resume()`, attempts | One item fails, the others finish; after the fix `resume()` redoes only the failed work. | §4.5, §4.6 |
| 7 | [`crash_recovery.py`](crash_recovery.py) | SIGKILL, run lease takeover, `interrupted` | A killed process loses only the step that was running; another process takes the run over. | §4.4 |
| 8 | [`incompatible_resume.py`](incompatible_resume.py) | `IncompatibleRun`, source-change warning | Resume never mixes step versions; bump a version and fork instead. | §4.5 |
| 9 | [`partial_runs.py`](partial_runs.py) | `until`, `items_filter`, `resume(items=...)` | Run the cheap steps now, one item end to end next, the rest later; status `partial`. | §4.3, §4.5 |
| 10 | [`fork_refresh.py`](fork_refresh.py) | `fork(refresh=...)` | A new run beside the old one reruns a step and its downstream and copies the rest. | §4.7 |
| 11 | [`fork_diff.py`](fork_diff.py) | automatic fork diff, `dry_run=True` | `fork()` finds what changed (versions, params, input files) and explains every rerun. | §4.7 |
| 12 | [`review_gates.py`](review_gates.py) | approve, edit, reject-and-revise | A person decides per item; a rejection note reaches the producer as `review_note`. | §4.8 |
| 13 | [`pin_and_cleanup.py`](pin_and_cleanup.py) | `pin()`, `cleanup()`, one logical run | Keep the runs that matter (verified copies), delete the rest safely. | §4.11 |
| 14 | [`notifications.py`](notifications.py) | webhooks, delivery states, retry | Tell Slack or any HTTP endpoint how a run ended; an outage never fails the run. | §4.9 |
| 15 | [`measurements.py`](measurements.py) | `measure=`, `reports/` | Timing, output sizes and resource use per step, and a summary per run. | §4.10 |
| 16 | [`records_and_traces.py`](records_and_traces.py) | `spans.jsonl`, `ctx.current_trace()`, `sink=` | Spans in the run folder, one trace from the workflow into the packages it calls. | §4.12 |
| 17 | [`read_api.py`](read_api.py) | `fk.open_runs`, `StepRecord`, detached runs | List and inspect runs (and review them) without the workflow's code. | §4.14 |
| 18 | [`s3_storage.py`](s3_storage.py) | S3 / fsspec storage | The same workflow with run folders in a bucket (`memory://` here). | §4.2 |
| 19 | [`gpu_batching.py`](gpu_batching.py) | `gpu:` tags, `GpuLease` | Neighbouring GPU steps share one lease, so a model loads once per batch. | §4.6 |
| 20 | [`cli_session.py`](cli_session.py) | the `hone-flow` command | Run, approve, resume and fork from a shell, with `--json` and exit codes. | §3 |
| 21 | [`video_pipeline.py`](video_pipeline.py) | a real pipeline shape | The OneShotStudio song-to-video pipeline with fake models: everything together. | §8 |
| 22 | [`run_labels.py`](run_labels.py) | `label=`, `ctx.set_run_label`, `run.set_label` | Give each run a human name and description, from the start or from a step. | §4.16 |
| 23 | [`progress.py`](progress.py) | INFO logs, `on_event=` | Follow a long run while it runs: its folder first, then every step and GPU lease wait. | §4.17 |
| 24 | [`revise_incrementally.py`](revise_incrementally.py) | `previous`, `ctx.previous_output()` | A rejected producer keeps what passed and fixes only what the note names. | §4.8 |
| 25 | [`fan_in.py`](fan_in.py) | `@wf.final_step`, `refresh={step: [items]}` | One step over every item's result (a workbook from all lessons), rebuilt when a fork refreshes one lesson. | §4.18 |
| 26 | [`select_items.py`](select_items.py) | `@wf.select_step`, `fk.Selection`, `not_selected` | Choose mid-run which items continue (a daily shortlist); the rest end `not_selected`. | §4.19 |
| 27 | [`produced_items.py`](produced_items.py) | `fk.Items`, `@wf.step(per=...)` | A step makes the items (chapters of an outline); one run takes them through their steps and a final cut. | §4.20 |

Extras: `s3_storage.py` needs `hone-flow[s3]`, `cli_session.py` needs `hone-flow[cli]`, and
`measurements.py` shows CPU and memory only with `hone-flow[metrics]` (it still runs without).
