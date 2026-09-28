"""Run folders: open one and see everything a run used and produced.

What: a tour of one run folder: ``manifest.json`` (the run), each step's ``metadata.json``, its
``inputs/`` snapshots and its ``output/`` files with readable names, plus ``spans.jsonl`` and
``reports/``.

How: run a small workflow, then read the folder with plain ``json`` and ``pathlib`` (no hone-flow API),
exactly as another tool or a person would. ``docs/run-format.md`` documents every key.

Why: the run folder is the source of truth. It is self-contained (inputs are copies, not references),
so you can move it, archive it or read it years later without the workflow's code; the format is
versioned (``format_version: "1"``).
"""

import json
import tempfile
from pathlib import Path
from typing import Any

import hone_flow as fk

tmp = Path(tempfile.mkdtemp(prefix="hone-flow-example-"))
(tmp / "01.md").write_text("first line\nsecond line\n")
wf = fk.Workflow("song_video", storage=tmp / "flows")


@wf.step()
def timeline(lyrics: fk.File) -> dict[str, Any]:
    lines = lyrics.path.read_text().splitlines()
    return {"lines": lines, "seconds": 4.0 * len(lines)}


@wf.step()
def poster(timeline: dict[str, Any], ctx: fk.Context) -> fk.File:
    path = ctx.new_file("poster.txt")
    path.write_text(f"{len(timeline['lines'])} lines")
    return fk.File(path)


run = wf.run([fk.Item("01", {"lyrics": fk.File(tmp / "01.md")})])
folder = Path(run.location)  # <storage>/song_video/runs/<run_id>/
for path in sorted(folder.rglob("*")):
    if path.is_file():
        print(path.relative_to(folder))

manifest = json.loads((folder / "manifest.json").read_text())
print("status:", manifest["status"], "| state:", manifest["state"])
meta = json.loads((folder / "timeline/item_01/metadata.json").read_text())
print("timeline input:", meta["inputs"]["lyrics"])  # {"from": "item_input", "files": {"lyrics.md": {...}}}
print("timeline output:", meta["outputs"]["timeline"])

assert manifest["format_version"] == "1"
assert (folder / "timeline/item_01/inputs/lyrics.md").read_text() == "first line\nsecond line\n"  # a copy
assert (folder / "timeline/item_01/output/timeline.json").is_file()  # <step>.json for JSON outputs
assert (folder / "poster/item_01/inputs/timeline.json").is_file()  # what poster received
assert (folder / "poster/item_01/output/poster.txt").read_text() == "2 lines"  # a file keeps its own name
assert (folder / "reports/summary.md").is_file()
assert not (folder / "lease.json").exists()  # only there while a process holds the run
