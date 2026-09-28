# Storage

Run folders live in a **storage root**: a local folder, an S3 prefix, any fsspec URL, or an object you
write. This page explains the forms `storage=` takes, what each storage guarantees, where steps do their
work, and how pinning and cleanup manage old runs.

Examples: [`s3_storage.py`](../examples/s3_storage.py),
[`pin_and_cleanup.py`](../examples/pin_and_cleanup.py), [`run_folder.py`](../examples/run_folder.py).

## `storage=`

| Value | Storage |
|---|---|
| a path (`"flows"`, `Path(...)`) or `"file:///abs/path"` | `fk.LocalStorage(path)` |
| `"s3://bucket/prefix"` or any other fsspec URL (`"memory://…"`, `"gs://…"`) | `fk.FsspecStorage(url)`: extra `s3` (fsspec and s3fs); other filesystems also need their fsspec package (e.g. gcsfs) |
| an object with the `RunStorage` methods | used as it is ([adapters](adapters.md#runstorage)) |

Without the `s3` extra, a URL raises `fk.HoneFlowError` ("pip install 'hone-flow[s3]'").

The storage is the storage **root**. Runs live under it at `<name>/runs/<run_id>/` and pinned copies at
`<name>/pinned_runs/<run_id>/`, where `<name>` is the workflow's name. One root can hold many
workflows; workflows with the same name under different roots are separate. `storage="s3://b/p"` and
`storage=fk.FsspecStorage("s3://b/p")` mean the same thing. Every manifest records the root (`storage`)
and the run folder (`location`).

```text
import hone_flow as fk

wf = fk.Workflow("clips", storage="s3://my-bucket/projects/demo")   # pip install "hone-flow[s3]"
# runs at s3://my-bucket/projects/demo/clips/runs/<run_id>/manifest.json ...
wf = fk.Workflow("clips", storage=fk.FsspecStorage("s3://my-bucket/projects/demo", profile="studio"))
```

S3 credentials come from the usual AWS configuration (environment, profile, instance role); extra fsspec
options are keyword arguments of `fk.FsspecStorage(url, **options)`.

## The same workflow on any storage

Everything works the same on every storage: runs, gates, resume, fork, pin, cleanup and the read API.
`memory://` is fsspec's in-memory filesystem, handy for trying S3-style storage without a network:

```python
import tempfile
import uuid
from pathlib import Path

import hone_flow as fk

url = f"memory://bucket-{uuid.uuid4().hex[:6]}/projects/demo"  # in production: "s3://your-bucket/..."
wf = fk.Workflow("clips", storage=url)


@wf.step()
def encode(name: str, ctx: fk.Context) -> fk.File:
    path = ctx.new_file(f"{name}.mp4")
    path.write_bytes(b"\x00fake video " + name.encode())
    return fk.File(path)


run = wf.run([fk.Item("01", {"name": "intro"})])
assert run.location == f"{url}/clips/runs/{run.run_id}/"
video = run.output("encode", "01", local_dir=Path(tempfile.mkdtemp()))  # downloaded
assert video.path.name == "intro.mp4" and video.path.read_bytes().startswith(b"\x00fake video")
new = run.fork(refresh=("encode",))
assert fk.open_runs(url, "clips").runs()[0].run_id == new.run_id
```

## What each storage guarantees

All three implement the same small `RunStorage` protocol over `/`-separated keys relative to the storage
root, for example `clips/runs/20260927T140311Z-3f9a1c/manifest.json`. Writes are atomic for readers (no
reader sees a half-written file). A conditional write takes `if_match` (the sha256 of the content the
writer last read) or `if_absent=True`, and raises `fk.WriteConflict` when the condition fails; the
manifest and the run lease use them.

### `fk.LocalStorage(root)` (core)

- Writes go to a temporary sibling file, are `fsync`ed and renamed into place.
- Conditional writes are checked under an `fcntl` lock on the key's folder, so they are atomic between
  processes on one machine (POSIX).
- `copy` (used by forks and pinning) makes a hardlink when source and target are on the same filesystem,
  else a copy. Every stored file is made read-only (`0444`), so writing to one never changes another
  run's file through a shared hardlink. `upload` always copies: your own files are never linked or changed.
- `run.output()` of an `fk.File` or `fk.Dir` returns the path inside the run folder (no copy). It is
  read-only: copy it before changing it.

```python
local = fk.Workflow("local_demo", storage=tempfile.mkdtemp())


@local.step()
def note(text: str, ctx: fk.Context) -> fk.File:
    path = ctx.new_file("note.txt")
    path.write_text(text)
    return fk.File(path)


run = local.run([fk.Item("01", {"text": "hello"})])
stored = run.output("note", "01").path
assert stored == Path(run.location, "note/item_01/output/note.txt")
assert stored.stat().st_mode & 0o777 == 0o444
```

### `fk.FsspecStorage(url, **options)` (extra `s3`)

- Any fsspec URL; `s3://` needs `s3fs` (both come with the `s3` extra).
- `copy` is the filesystem's own copy: a server-side CopyObject on S3, so forking and pinning large
  media does not download it.
- `if_absent` uses the backend's exclusive create where it has one (S3 conditional writes, `memory://`),
  else checks and then writes.
- `if_match` reads, compares and writes. **Limitation:** on a backend without conditional writes this
  check is not atomic across machines. The [run lease](run-folders.md#the-run-lease) is what prevents two
  writers on one run; the check only catches mistakes.
- Directory listings are not cached (`use_listings_cache=False` unless you pass it): other processes
  change run folders, and a stale listing could hide a lease.

### `hone_flow.testing.MemoryStorage()` (for tests)

A dict of key to bytes with exact semantics, in one process. Its `files` attribute shows every key.

```python
from hone_flow.testing import MemoryStorage

memory = MemoryStorage()
mem = fk.Workflow("mem_demo", storage=memory)


@mem.step()
def double(x: int) -> int:
    return 2 * x


run = mem.run([fk.Item("a", {"x": 21})])
assert run.output("double", "a") == 42
assert f"mem_demo/runs/{run.run_id}/double/item_a/output/double.json" in memory.files
```

## Browsing files

Tools that show run folders (a file browser, a video player) read them through three helpers in
`hone_flow.storage` that work with every storage: `list_dir(storage, prefix)` lists one folder level as
`fk.Entry(name, is_dir, size)` items, `file_size(storage, key)` gives a file's size, and
`read_range(storage, key, start, length)` reads a byte range without fetching the whole file (an HTTP
`Range` request on S3), for seeking in a large video. `LocalStorage`, `FsspecStorage` and
`MemoryStorage` implement them directly ([adapters](adapters.md#runstorage)).

```python
from hone_flow.storage import file_size, list_dir, read_range

browsed = fk.Workflow("browsed", storage=tempfile.mkdtemp())


@browsed.step()
def clip(ctx: fk.Context) -> fk.File:
    path = ctx.new_file("clip.bin")
    path.write_bytes(bytes(range(100)))
    return fk.File(path)


shown = browsed.run([fk.Item("a")])
output = f"browsed/runs/{shown.run_id}/clip/item_a/output"
assert list_dir(browsed.storage, output) == [fk.Entry("clip.bin", False, 100)]
assert file_size(browsed.storage, f"{output}/clip.bin") == 100
assert read_range(browsed.storage, f"{output}/clip.bin", 10, 3) == bytes([10, 11, 12])
```

## Where steps work

Steps always see local paths. For each call hone-flow makes a local work folder; each step attempt gets
its own subfolder, where `ctx.new_file` / `ctx.new_dir` create paths and where inputs from remote storage
are downloaded. Outputs are uploaded when the attempt is committed, and the work folder is removed at the
end of the call. It is created under `$HONE_FLOW_WORKDIR` when set (for example a fast local disk),
else in the system's temporary folder.

```python
import os

os.environ["HONE_FLOW_WORKDIR"] = str(Path(tempfile.mkdtemp()))
seen: list[Path] = []
scratch = fk.Workflow("scratch", storage=tempfile.mkdtemp())


@scratch.step()
def draw(name: str, ctx: fk.Context) -> fk.File:
    path = ctx.new_file(f"{name}.txt")
    seen.append(path)
    path.write_text(name)
    return fk.File(path)


scratch.run([fk.Item("01", {"name": "frame"})])
assert seen[0].is_relative_to(os.environ["HONE_FLOW_WORKDIR"])
assert not seen[0].exists()  # removed at the end of the call; the output is in the run folder
del os.environ["HONE_FLOW_WORKDIR"]
```

## Pinning and cleanup

Run folders accumulate. `cleanup` deletes old ones; `pin` keeps the ones that matter.

- `run.pin()` copies a **finished** run (status `completed` or `failed`, nobody holding it) from
  `runs/<id>/` to `pinned_runs/<id>/`, checks every copied file's sha256 against the original, and marks
  both manifests `pinned: true`. The pinned `manifest.json` is written last, so a half-finished pin is
  never listed. A pinned copy is an immutable archive: resume and reviews on it raise; `fork()` works.
  A run is pinned once; pinning again raises `fk.HoneFlowError`. To archive a later state, fork and pin
  the fork.
- Listings show **one logical run** per run id: `pinned=True` when a pinned copy exists; the `runs/` copy is
  used while it exists. `open_run(id)` opens `runs/<id>`, else `pinned_runs/<id>`, else raises
  `fk.RunNotFound`.
- `wf.cleanup(*, keep_last=None, older_than=None, dry_run=False)` deletes run folders under `runs/`
  only. A run is deleted when it is outside the newest `keep_last` runs (by creation time) **and** older
  than `older_than` (a `timedelta`); each condition applies when given, and at least one is required.
  Never deleted: anything under `pinned_runs/`, and a run whose lease is live. It returns
  `fk.CleanupReport(deleted, kept, locked)` (run ids); `dry_run=True` returns the same report and
  deletes nothing.

```python
from datetime import timedelta

shelf = fk.Workflow("pin_demo", storage=tempfile.mkdtemp())


@shelf.step()
def mix(track: str) -> str:
    return f"mixed {track}"


runs = [shelf.run([fk.Item("01", {"track": f"take {n}"})]) for n in (1, 2, 3)]
runs[0].pin()
assert set(shelf.cleanup(keep_last=1, dry_run=True).deleted) == {runs[0].run_id, runs[1].run_id}
report = shelf.cleanup(keep_last=1, older_than=timedelta(0))
assert set(report.deleted) == {runs[0].run_id, runs[1].run_id}  # the pinned run's runs/ copy too
assert {s.run_id: s.pinned for s in shelf.runs()} == {runs[2].run_id: False, runs[0].run_id: True}

archive = shelf.open_run(runs[0].run_id)  # the pinned copy
assert archive.location.endswith(f"/pinned_runs/{runs[0].run_id}/")
assert archive.output("mix", "01") == "mixed take 1"
assert archive.fork(refresh=("mix",)).status == "completed"
```

The CLI has the same operations: `hone-flow pin RUN_ID` and
`hone-flow cleanup --keep-last 20 --older-than 30d --dry-run` ([CLI](cli.md)).
