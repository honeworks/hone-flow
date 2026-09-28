"""Fork with refresh: rerun one step (and everything after it) in a new run beside the old one.

What: ``run.fork(refresh=("shotlist",))`` creates a new run id next to the source. ``shotlist`` and every
step downstream of it run again; everything else is copied from the source, labeled ``reused`` with
``reused_from`` pointing at the source run.

How: open the source (``wf.open_run(run_id)``), call ``fork(refresh=(...))``. Options: ``items=[...]``
to fork only some items (or give ``fk.Item`` objects with new inputs), ``params={...}`` merged over the
source's params. The source is never changed.

Why: this is how you get a new sample of a non-deterministic step, or rerun one step after changing a
prompt, without paying again for the steps before it. Reused results are copies (on S3 a server-side
copy), so the fork stands alone even if the source is later deleted.
"""

import tempfile
from typing import Any

import hone_flow as fk

calls: list[str] = []
wf = fk.Workflow("refresh_demo", storage=tempfile.mkdtemp())


@wf.step()
def lyrics(topic: str, ctx: fk.Context) -> str:
    calls.append(f"lyrics/{ctx.item_id}")
    return f"a song about {topic}"


@wf.step(deterministic=False)
def shotlist(lyrics: str, ctx: fk.Context) -> dict[str, Any]:
    calls.append(f"shotlist/{ctx.item_id}")
    return {"shots": lyrics.split(), "sample": ctx.seed % 1000}  # stands in for an LLM sample


@wf.step()
def storyboard(shotlist: dict[str, Any]) -> int:
    calls.append("storyboard")
    return len(shotlist["shots"])


source = wf.run([fk.Item("01", {"topic": "rain"})])
calls.clear()
new = source.fork(refresh=("shotlist",))
print("new run", new.run_id, "beside", source.run_id)
print("reran:", calls)
lyrics_record = new.steps("lyrics", "01")[0]
print("lyrics:", lyrics_record.labels, "from", lyrics_record.reused_from)
print("samples:", source.output("shotlist", "01")["sample"], "->", new.output("shotlist", "01")["sample"])

assert calls == ["shotlist/01", "storyboard"]  # the refreshed step and its downstream
assert lyrics_record.labels == ["reused"] and lyrics_record.reused_from == source.run_id
assert new.manifest["fork_of"]["run_id"] == source.run_id
assert source.output("shotlist", "01") != new.output("shotlist", "01")  # a new sample
