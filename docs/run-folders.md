# Run folders

Every run is a folder that holds everything the run used and produced. It is the source of truth: there
is no database beside it. This page explains how the folder is laid out and how hone-flow keeps it
consistent when processes crash. The keys of each JSON file are in the [run format](run-format.md).

Examples: [`run_folder.py`](../examples/run_folder.py),
[`crash_recovery.py`](../examples/crash_recovery.py),
[`failure_and_resume.py`](../examples/failure_and_resume.py).

## Layout

```text
<storage>/<workflow name>/
  runs/<run_id>/
    manifest.json        the run: identity, items, params, step table, state of every step, calls
    lease.json           only while a process holds the run
    spans.jsonl          the records of every call on this run
    reports/             timing.json  output_sizes.json  system_resources.json  summary.md
    <global step>/       metadata.json  inputs/  output/  attempts/<n>/output/
    <item step>/item_<item id>/
                         metadata.json  inputs/  output/  attempts/<n>/output/
  pinned_runs/<run_id>/  a verified copy of a finished run (same layout)
```

- Run ids are `<UTC time %Y%m%dT%H%M%SZ>-<6 random hex>`, for example `20260927T140311Z-3f9a1c`: unique
  and sortable by creation time.
- A step folder exists once the step started. Steps that are `pending`, `skipped` or `blocked` have no
  folder; the manifest's `state` table lists them.
- The folder is self-contained: inputs are copies, not references. Move it, archive it or open it years
  later; `fk.open_runs` reads a copied folder like the original ([read API](read-api.md)).

```python
import json
import tempfile
from pathlib import Path

import hone_flow as fk

Path("01.md").write_text("first line\nsecond line\n")
wf = fk.Workflow("song_video", storage=tempfile.mkdtemp())


@wf.step()
def timeline(lyrics: fk.File) -> dict:
    return {"lines": lyrics.path.read_text().splitlines()}


@wf.step()
def poster(timeline: dict, ctx: fk.Context) -> fk.File:
    path = ctx.new_file("poster.txt")
    path.write_text(f"{len(timeline['lines'])} lines")
    return fk.File(path)


run = wf.run([fk.Item("01", {"lyrics": fk.File("01.md")})])
folder = Path(run.location)  # <storage>/song_video/runs/<run_id>/
print(sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file()))
assert (folder / "timeline/item_01/inputs/lyrics.md").read_text() == "first line\nsecond line\n"
assert (folder / "timeline/item_01/output/timeline.json").is_file()
assert (folder / "poster/item_01/inputs/timeline.json").is_file()
assert (folder / "poster/item_01/output/poster.txt").read_text() == "2 lines"
meta = json.loads((folder / "poster/item_01/metadata.json").read_text())
assert meta["status"] == "done" and meta["inputs"]["timeline"]["from"] == "step:timeline"
```

## Input snapshots

Before a step runs, each of its inputs is written to its `inputs/` folder, and the step receives that
snapshot (a local path under `inputs/`, or a download of it), never the original:

| Input | Snapshot |
|---|---|
| another step's output (or a global step's) | `inputs/<parameter name><suffix of the source file>`, e.g. `inputs/timeline.json` |
| an item file (`fk.File`) | `inputs/<parameter name><suffix>`, e.g. `inputs/lyrics.md` |
| a folder (`fk.Dir`) | `inputs/<parameter name>/` with its contents |
| a JSON item value | `inputs/<parameter name>.json` |
| params, the context | not files; params are in `metadata.json` |

An item file or folder is hashed when the run starts (`wf.run`) and must still have that content when a
step snapshots it; otherwise the step fails with "changed since the run started". Resume therefore never
mixes old and new inputs; a [fork](resume-and-fork.md) notices the change (`input_changed:<name>`) and
reruns what depends on it.

## Output names

| Output | File in `output/` |
|---|---|
| JSON data or a Pydantic model | `<output name>.json` (the output name is the step name, or a name from `outputs=`) |
| `fk.File` | the file's own name (`poster.txt`) |
| `fk.Dir` | the folder's own name and contents |
| a registered serializer | `<output name>.<extension>` |

JSON is canonical: sorted keys, UTF-8, two-space indent. Hashes (sha256) check integrity and compare
contents; they never name files.

## The commit protocol

A step attempt writes its output files first and its `metadata.json` **last**. `metadata.json` is the
commit marker: a step folder without it did not happen.

1. The manifest marks the step `running`.
2. The step runs in a local work folder; its inputs are already in `inputs/`.
3. On success, outputs go to `output/`, then `metadata.json` (`status: done`, or `awaiting_review` for a
   gate), then the manifest's `state`.
4. On failure, whatever the step wrote in its work folder goes to `attempts/<n>/output/`, then
   `metadata.json` with `status: failed`, the error message and the traceback. `output/` only ever holds
   a successful result.

Manifest writes are conditional: a write succeeds only if the manifest is still the one this process last
read or wrote (compared by sha256). Otherwise `fk.WriteConflict` is raised, which means a bug or a second
writer without the lease.

The manifest's `state` table is a summary for fast listing; each `metadata.json` is the truth. When the
two disagree (a crash between the two writes) the metadata wins and the next resume repairs the manifest.

## The run lease

Only one process at a time may change a run. Every call that changes it (run, resume, fork's new run,
approve, edit, reject, pin, retry of notifications, cleanup) first creates `lease.json` (a write that fails
if the file exists) and deletes it at the end. While a call runs, a background thread refreshes the
lease's `heartbeat_at` and `expires_at` every 10 s (the lease lives 60 s without a refresh).

A lease is **live** when its host is this host and its process is alive, or its host is another host and
`expires_at` is in the future. A live lease held by someone else raises `fk.RunLocked` (the message names
the host, the pid and the expiry). A **stale** lease is taken over: the running steps of the dead process
become `interrupted`, and every step folder is cleaned up to match its metadata:

- a step folder without `metadata.json` is deleted;
- an `attempts/<n>/` folder that `metadata.json` does not list is deleted;
- `output/` of a step whose metadata has no current result is deleted.

After this, every step folder is exactly what its metadata says, and `resume()` reruns the interrupted
steps. A crash loses at most the steps that were running. See
[`crash_recovery.py`](../examples/crash_recovery.py), which kills a process with SIGKILL and resumes it.

## Attempts

The top-level fields of `metadata.json` describe the latest attempt; `attempts` lists the earlier ones,
and their files are kept under `attempts/<n>/output/`. An attempt ends up in the list because it:

| Attempt status | Why |
|---|---|
| `failed` | the step raised; a retry (on resume) is attempt `n + 1` |
| `rejected` | a person rejected the gate that reviews it ([gates](gates.md)); the gate's own output is also `rejected` |
| `replaced` | an edit replaced a gate's output, or a rejection reset a step downstream of the rejected one |

```python
flaky = {"on": True}
retry = fk.Workflow("retry_demo", storage=tempfile.mkdtemp())


@retry.step()
def draft(text: str, ctx: fk.Context) -> str:
    ctx.new_file("notes.txt").write_text(f"attempt {ctx.attempt}")
    if flaky["on"]:
        raise ValueError("model timed out")
    return text.upper()


run = retry.run([fk.Item("01", {"text": "hello"})])
assert run.status == "failed"
assert run.steps("draft", "01")[0].error["message"] == "ValueError: model timed out"

flaky["on"] = False
run.resume()
record = run.steps("draft", "01")[0]
assert (run.status, record.attempt) == ("completed", 2)
assert [(a["attempt"], a["status"]) for a in record.attempts] == [(1, "failed")]
assert Path(run.location, "draft/item_01/attempts/1/output/notes.txt").read_text() == "attempt 1"
```

## Reading run folders without hone-flow

The JSON files are a public, versioned format (`format_version: "1"`): other tools may read them directly.
Adding optional keys keeps version `"1"`; removing or changing a key needs `"2"`. `reports/*` are for
people and may change between versions. Every key is documented in [run-format.md](run-format.md).
