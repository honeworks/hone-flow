# Run folder format (`format_version: "1"`)

Every hone-flow run is a folder you can open and understand without hone-flow: what each step received,
what it produced, which code and settings produced it, how long it took, what a person decided and what
failed. Other tools (hone-lens, a future memory package, your own scripts) may read these files directly.
The JSON files below are a **public, versioned format**:

- Adding optional keys keeps `format_version: "1"`; removing a key or changing its meaning needs `"2"`.
- Readers ignore keys they do not know. hone-flow refuses a `format_version` it does not know
  (`HoneFlowError: ... upgrade hone-flow`).
- All JSON is UTF-8 with sorted keys and a two-space indent. Times are ISO-8601 UTC with milliseconds.
- Hashes are sha256 hex. They check integrity and compare contents; they never name files.
- `reports/*` are for people and may change between versions; they are not part of the format.

The Pydantic models in `hone_flow/run_format.py` define these files; a test checks that every example
below validates against its model and lists exactly the model's keys.

## Layout

```text
<storage>/<workflow name>/
  runs/<run_id>/
    manifest.json        the run: identity, items, steps, state summary, notifications, calls
    lease.json           only while a process holds the run
    spans.jsonl          the spans of every call on this run, one JSON object per line
    reports/             timing.json  output_sizes.json  system_resources.json  summary.md
    <global step>/       metadata.json  inputs/  output/  attempts/<n>/output/
    <item step>/item_<item id>/
                         metadata.json  inputs/  output/  attempts/<n>/output/
  pinned_runs/<run_id>/  a verified copy of a finished run (same layout)
```

- `<storage>` is the `storage=` given to `fk.Workflow` (a local path or a URL such as
  `s3://bucket/prefix`); `<workflow name>` is its `name`.
- Run ids are `<UTC time %Y%m%dT%H%M%SZ>-<6 random hex>`: unique, sortable by creation time.
- A step folder exists once the step started. **`metadata.json` is written last and is the commit
  marker:** a step folder without it did not happen (hone-flow deletes it on the next resume).
- `inputs/` holds copies of what the step received: `inputs/<parameter><suffix of the source file>`
  (`inputs/timeline.json`, `inputs/lyrics.md`), a folder as `inputs/<parameter>/`, a JSON item value as
  `inputs/<parameter>.json`. Params are in `metadata.json`, not files.
- `output/` holds the current result: `<output name>.json` for JSON and Pydantic outputs, the file's own
  name for `fk.File`, the folder's own name for `fk.Dir`, `<output name>.<extension>` for registered
  serializers.
- `attempts/<n>/output/` holds the files of earlier attempts (failed, rejected, replaced by an edit).
- On S3 the layout is the key prefixes; empty folders have no object.

## `manifest.json`

<!-- model: Manifest -->
```json
{
  "attempts": {"shotlist/01": 2},
  "calls": [
    {"ended_at": "2026-09-27T14:05:40.002Z", "host": "studio", "items": null, "kind": "run", "pid": 4242,
     "span_id": "a3ce929d0e0e4736", "started_at": "2026-09-27T14:03:11.120Z", "until": null}
  ],
  "created_at": "2026-09-27T14:03:11.120Z",
  "description": "first draft, dark and slow",
  "fork_of": null,
  "format_version": "1",
  "hone_flow_version": "0.1.0",
  "items": [
    {"id": "01", "items_from": null, "inputs": {
      "lyrics": {"kind": "file", "path": "/home/me/songs/01.md", "sha256": "9f2c…", "size": 812,
                 "snapshot": null, "value": null},
      "mood": {"kind": "json", "path": null, "sha256": null, "size": null, "snapshot": null, "value": "calm"}}}
  ],
  "label": "Rain on a tin roof",
  "location": "s3://hone-flow/projects/oneshotstudio/song_video/runs/20260927T140311Z-3f9a1c/",
  "measure": ["timing", "output_sizes"],
  "notifications": [
    {"created_at": "2026-09-27T14:05:40.010Z",
     "deliveries": {"ops": {"at": "2026-09-27T14:05:40.300Z", "attempts": 1, "browse_url": null,
                            "error": null, "kind": "http", "state": "delivered", "url_env": "OPS_WEBHOOK_URL"}},
     "event": "run.awaiting_review", "id": "20260927T140311Z-3f9a1c/run.awaiting_review/1",
     "summary": "1 item awaiting review at review_shotlist"}
  ],
  "params": {"style": "silhouette"},
  "pinned": false,
  "pinned_at": null,
  "run_id": "20260927T140311Z-3f9a1c",
  "seed": 0,
  "state": {"review_shotlist/01": "awaiting_review", "shotlist/01": "done", "style_guide": "done",
            "timeline/01": "done"},
  "status": "awaiting_review",
  "steps": [
    {"deterministic": false, "inputs": ["timeline", "style_guide"], "kind": "step", "name": "shotlist",
     "outputs": ["shotlist"], "per": null, "resources": "gpu:ollama", "source_hash": "5d41…", "version": "1"}
  ],
  "storage": "s3://hone-flow/projects/oneshotstudio",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "updated_at": "2026-09-27T14:05:40.300Z",
  "warnings": [],
  "workflow": "song_video",
  "workflow_version": "1"
}
```

| Key | Meaning |
|---|---|
| `format_version` | `"1"` |
| `run_id`, `workflow`, `workflow_version` | identity; `workflow` is the workflow name |
| `storage`, `location` | the storage root the run was created in, and the URL / path of this run folder |
| `hone_flow_version` | the hone-flow version that created the run |
| `created_at`, `updated_at` | creation time; time of the last manifest write (listings filter on it) |
| `status` | run status: `running`, `completed`, `failed`, `awaiting_review`, `partial`, `interrupted` |
| `seed` | the run seed (step seeds derive from it) |
| `trace_id` | the run's trace; resume continues it, a fork starts its own |
| `params` | run params (secret-looking values replaced by `***`) |
| `items` | items and their inputs: `kind` `json` (`value`), `file` or `dir` (absolute original `path`, `sha256`, `size`; in a fork whose original file is gone, `snapshot` is the storage key of the source run's copy); `items_from` names the global step that produced the item (`null` for the run's own items), and produced items are added when that step commits |
| `steps` | the step table: name, `kind` (`step`, `gate`, `global`, `final`, `select`), version, source hash, resources, `deterministic`, `inputs` (parameters fed by other steps or item inputs), `outputs`, `per` (the producing global step of an item step over produced items, else `null`) |
| `state` | summary of step states by `"<step>"` (global) or `"<step>/<item>"`; each `metadata.json` is the truth |
| `measure` | the measurements enabled for the run |
| `warnings` | e.g. `{"kind": "source_changed_version_unchanged", "step", "old", "new", "at"}` |
| `fork_of` | `null`, or `{"run_id": <source run>, "plan": [{"item", "step", "action", "reason"}]}` |
| `pinned`, `pinned_at` | whether a verified copy exists under `pinned_runs/` |
| `notifications` | events (`run.completed`, `run.failed`, `run.awaiting_review`) with a stable `id` and one delivery entry per destination: `state` (`pending`, `delivered`, `failed`), `attempts`, `error`, `at`, and the destination's `kind`, `url_env` (the variable's name, never the URL) and `browse_url` |
| `calls` | one entry per `run` / `resume` / `fork` call: times, host, pid, `until`, `items`, the call's run span id |
| `attempts` | optional: `"<step>/<item>"` (or `"<step>"`) → the latest attempt number, only for steps tried more than once; written when an attempt commits, so a listing shows retries without reading step metadata |
| `label`, `description` | optional: a human name and description of the run (`null` or missing when unset; secret-looking text is `***`) |

Step states: `pending`, `running` (manifest only), `done`, `failed`, `blocked` (an upstream step
failed), `skipped` (outside the call's selection), `awaiting_review` (a gate waiting for a person),
`interrupted` (its process died while it ran), `not_selected` (a select step left the item out; no step
folder, final like `done`).

## `metadata.json` (one per step folder)

<!-- model: StepMetadata -->
```json
{
  "attempt": 2,
  "attempts": [
    {"attempt": 1, "ended_at": "2026-09-27T14:04:02.000Z", "error": null, "note": "darker lighting",
     "outputs": {"shotlist": {"files": {"shotlist.json": {"sha256": "1b4f…", "size": 1201}}, "type": "json"}},
     "started_at": "2026-09-27T14:03:58.000Z", "status": "rejected"}
  ],
  "deterministic": false,
  "duration_ms": 5120,
  "ended_at": "2026-09-27T15:10:05.120Z",
  "error": null,
  "format_version": "1",
  "inputs": {
    "style_guide": {"files": {"style_guide.json": {"sha256": "77aa…", "size": 64}}, "from": "global:style_guide"},
    "timeline": {"files": {"timeline.json": {"sha256": "c3d9…", "size": 311}}, "from": "step:timeline"}
  },
  "item": "01",
  "kind": "step",
  "labels": [],
  "measurements": {"output_sizes": {"total_bytes": 1234}, "timing": {"duration_ms": 5120, "lease_wait_ms": 3}},
  "outputs": {"shotlist": {"files": {"shotlist.json": {"sha256": "e0f1…", "size": 1234}}, "type": "json"}},
  "params": {},
  "resources": "gpu:ollama",
  "reused_from": null,
  "review_note": "darker lighting",
  "reviews": [],
  "run_id": "20260927T140311Z-3f9a1c",
  "seed": 1234567,
  "source_attempt": null,
  "source_hash": "5d41…",
  "span_id": "00f067aa0ba902b7",
  "started_at": "2026-09-27T15:10:00.000Z",
  "status": "done",
  "step": "shotlist",
  "version": "1"
}
```

| Key | Meaning |
|---|---|
| `run_id`, `step`, `item`, `kind` | which step execution this is (`item` is `null` for a global, final or select step) |
| `status` | `done`, `failed`, `awaiting_review` (a gate), `pending` (its result was sent back by a rejection) |
| `version`, `source_hash`, `deterministic`, `resources` | the step definition that ran |
| `params` | the run params this step declares (secret-stripped) |
| `seed` | `ctx.seed` of this attempt |
| `attempt` | number of the latest attempt (1-based; `1` for a result a fork copied) |
| `inputs` | per parameter: `from` (`step:<name>`, `global:<name>`, `final:<name>`, `select:<name>`, `items:<name>` for a final or select step's per-item input, `item_input`, `item_value`) and the files under `inputs/` with sha256 and size (`<parameter>/<item id>/<file>` for `items:`) |
| `outputs` | per output name: `type` (`json`, `pydantic:<module>:<qualname>`, `file`, `dir`, `custom:<serializer>`) and the files under `output/` |
| `labels` | `reused` (copied by a fork), `approved`, `edited` |
| `reused_from`, `source_attempt` | for a step a fork copied: the source run id and the source attempt |
| `review_note` | the latest rejection note passed to the step as `review_note` |
| `reviews` | gate decisions: `decision` (`approved`, `edited`, `rejected`), `actor`, `actor_kind` (`person`, or `automated` when a program decided; missing means `person`), `note`, `at`, `attempt` (the gate attempt reviewed) |
| `error` | for a failed latest attempt: `message` and secret-stripped `traceback` |
| `attempts` | earlier attempts: `attempt`, `status` (`failed`, `rejected`, `replaced`, see below), times, `note`, `error`, `outputs` (files under `attempts/<n>/output/`) |
| `started_at`, `ended_at`, `duration_ms` | the latest attempt's times |
| `measurements` | enabled measurements: `timing`, `output_sizes`, `system` (resource summary) |
| `span_id` | the span of the latest attempt in `spans.jsonl` (`hone.flow.step`, or `hone.flow.gate` for a gate); `null` after an edit |

The top-level fields describe the latest attempt. A failed latest attempt has `status: failed`, an
`error`, and its files (whatever the step wrote in its work folder) under `attempts/<attempt>/output/`;
`output/` only ever holds a successful result.

An earlier attempt's `status` says why it is no longer current
([run folders](run-folders.md#attempts), [gates](gates.md#reject-and-revise)):

| `attempts[].status` | Meaning |
|---|---|
| `failed` | the step raised; the retry on resume is the next attempt |
| `rejected` | a person rejected the gate that reviews this step's output (with `note`); the gate's own output is retired as `rejected` too |
| `replaced` | an edit replaced the gate's output, or a rejection reset this step because it is downstream of the rejected producers (for a rejected global producer: the steps of every item) |

## `lease.json`

<!-- model: Lease -->
```json
{
  "acquired_at": "2026-09-27T14:03:11.120Z",
  "expires_at": "2026-09-27T14:04:21.120Z",
  "heartbeat_at": "2026-09-27T14:03:21.120Z",
  "host": "studio",
  "owner": "0b8c4d7e-1f0a-4c55-9d1e-6a2b3c4d5e6f",
  "pid": 4242
}
```

Present only while a process holds the run (run, resume, fork, approve, edit, reject, pin,
retry_notifications, cleanup). The holder refreshes `heartbeat_at` and `expires_at` every 10 s (60 s TTL). A lease
is live when its host is this host and its pid is alive, or its host is another host and `expires_at` is
in the future; otherwise the next process takes it over and marks the run's `running` steps
`interrupted`.

## `spans.jsonl`

One span per line (the honeworks span format, OpenTelemetry field names): `hone.flow.run` (one per call),
`hone.flow.step` (one per step and item the call touched) and `hone.flow.gate` (gate executions and every
review decision), with `hone.flow.*` attributes. [records.md](records.md) lists
the span names, attributes and events.
