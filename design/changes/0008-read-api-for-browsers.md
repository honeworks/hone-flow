# 0008: Read API for browsers and dashboards (listing without reading everything, file access)

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building run-browser (a demo app built on the honeworks packages), a read-only web app over run folders, local and on
S3, built only on `fk.open_runs` and the storage classes. Three things it needs are missing, so it works
around them with hone-flow internals or attributes that are not part of the documented API:

1. **Listing runs is all-or-nothing.** `RunHistory.runs()` lists every key under `<name>/runs/` and
   `<name>/pinned_runs/` recursively (every media file of every run: tens of thousands of keys on S3
   for a few hundred video runs) and reads and validates every manifest, then sorts. A table that shows
   50 runs of 5,000 needs the run ids (one-level listing; ids sort by creation time) and the manifests of
   one page. `updated_since=` does not help: it still reads every manifest to filter.
2. **`RunStorage` has no one-level listing, size or byte range.** A file browser needs the children of a
   folder, and serving a 500 MB video to a `<video>` element needs `Range` requests (seeking) without
   downloading the file. The app uses `LocalStorage.path()` and `FsspecStorage.fs` / `.root` directly.
3. **A few facts need every step's `metadata.json`.** "Steps with more than one attempt" and "is this
   `running` run actually held by a process?" are not in the manifest or `RunSummary`; the app reads
   step metadata per row and `lease.json` itself (`is_live` in `run_lease.py` is internal).

## Options

1. **Add small read methods** (no format change):
   - `RunHistory.run_ids() -> list[str]`: newest first, from one-level listings (`runs/` wins over
     `pinned_runs/`), no manifest read.
   - `RunHistory.summaries(run_ids) -> list[RunSummary]`: reads only those manifests (in parallel on
     remote storage); `runs()` becomes `summaries(run_ids())` plus the `updated_since` filter.
   - `RunStorage.list_dir(prefix) -> list[Entry(name, is_dir, size)]`, `size(key)`,
     `read_range(key, start, length)` (optional protocol methods with a slow fallback over `list` /
     `read_bytes` for storages that do not have them); `LocalStorage` and `FsspecStorage` implement them
     natively.
   - `Run.lease() -> LeaseInfo | None` (`host`, `pid`, `expires_at`, `live`) using `is_live`.
2. **Also extend `RunSummary` / the manifest's `state` summary** with per-step attempt counts
   (`attempts: {"<step>/<item>": n}` for steps with n > 1), written when a step commits: an optional key,
   `format_version` stays `"1"`.
3. Leave it to tools (today): each browser or dashboard reaches into `FsspecStorage.fs` and re-derives
   lease liveness.

## Decision

Proposed: option 1, and option 2's optional `attempts` key in the manifest. They are additive, keep the
format at `"1"`, and make `runs()` itself cheaper for every caller (hone-lens' flow adapter, the CLI's
`runs`).

## Consequences

- `RunStorage` implementations outside hone-flow keep working (new methods optional, with fallbacks);
  `check_run_storage` tests the new ones when present.
- run-browser drops its storage workarounds and the per-row step-metadata reads for its "Retries"
  column; the column becomes sortable.
- docs: `read-api.md` (new methods), `storage.md` (the protocol additions), `run-format.md` (`attempts`).

## Implementation notes

- The optional storage methods are not part of the `RunStorage` Protocol type (that would make existing
  implementations fail type checks); callers use `hone_flow.storage.list_dir`, `file_size` and
  `read_range`, which call the method when a storage has it and fall back otherwise.
- `run_ids()` sorts by id, which orders runs by creation time to the second; `runs()` keeps sorting by
  `created_at` (milliseconds). `summaries()` detects a pinned copy with one `exists` call per run.
- `cleanup` now lists run ids one level deep too (no recursive listing of every file).
- The manifest's optional `attempts` key is kept by every attempt commit, edits and recovery.
- `fk.Entry` and `fk.LeaseInfo` are frozen dataclasses. AC-35 in `design/current.md` §7.
