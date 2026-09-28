"""Items made by a step: dynamic fan-out inside one run.

What: an outline step decides how many chapters an episode has; each chapter then becomes an item with
its own steps (narration, pictures), and a final step cuts the episode from all of them, in one run.

How: a ``@wf.global_step`` annotated ``-> fk.Items`` returns ``fk.Items([fk.Item(...), ...])``; steps
declared ``@wf.step(per="<that step>")`` run once per produced item, receiving its inputs like any item
step. The items join the manifest (``items_from``) when the producer commits. A fork compares produced
items by id and inputs, so a changed chapter reruns only that chapter's steps (and the final step).

Why: without it an app runs one workflow for the outline, builds items itself, starts a second run for
the chapters and a third to edit them, and keeps a state file that links the three. One run keeps
resume, fork, GPU batching and the records in one folder.
"""

import tempfile

import hone_flow as fk
from hone_flow.testing import FakeGpuLease

calls: list[str] = []
wf = fk.Workflow("episode", storage=tempfile.mkdtemp(), gpu=FakeGpuLease())


@wf.global_step()
def outline(concept: fk.Param[str], titles: fk.Param[list[str]]) -> fk.Items:
    calls.append("outline")
    return fk.Items(fk.Item(f"ch{n}", {"title": t, "concept": concept}) for n, t in enumerate(titles, 1))


@wf.step(per="outline", resources="gpu:tts")
def narrate(title: str, concept: str, ctx: fk.Context) -> str:
    calls.append(f"narrate/{ctx.item_id}")
    return f"{concept}, part '{title}'"


@wf.step(per="outline", resources="gpu:image")
def picture(narrate: str, ctx: fk.Context) -> str:
    calls.append(f"picture/{ctx.item_id}")
    return f"[picture for {narrate}]"


@wf.final_step()
def cut(picture: dict[str, str]) -> list[str]:
    calls.append("cut")
    return list(picture.values())


run = wf.run([], params={"concept": "tides", "titles": ["the moon", "the shore"]})
print(calls)
print(run.output("cut"))
assert calls == ["outline", "narrate/ch1", "narrate/ch2", "picture/ch1", "picture/ch2", "cut"]  # batched
assert [item["items_from"] for item in run.manifest["items"]] == ["outline", "outline"]

calls.clear()
redo = run.fork(params={"titles": ["the moon", "the wind"]})  # chapter 2 changed
print(calls)
assert calls == ["outline", "narrate/ch2", "picture/ch2", "cut"]  # chapter 1 is copied
assert redo.steps("picture", "ch1")[0].labels == ["reused"]
