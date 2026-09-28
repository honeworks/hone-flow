"""Run labels: a human name and description for each run.

What: every run can carry a ``label`` and a ``description``, shown by listings (``RunSummary``, the
``hone-flow runs`` command), notifications and run browsers. A run id says when a run started; the
label says what it is.

How: pass ``label=`` / ``description=`` to ``wf.run`` (or ``run.fork``); name the run from inside a step
with ``ctx.set_run_label(...)`` when the name is only known there (applied when the step's result is
committed); rename it later with ``run.set_label(...)`` (it takes the run lease, like a review). A fork
defaults to ``"<source label> (fork)"``.

Why: runs of one app look alike (same status, same item count). The app that made the run knows its
name best (the song's title, the course title), so it writes the name into the run once, instead of
every tool guessing it from step outputs.
"""

import tempfile

import hone_flow as fk

storage = tempfile.mkdtemp()
wf = fk.Workflow("songs", storage=storage)


@wf.global_step()
def idea(topic: fk.Param[str], ctx: fk.Context) -> dict[str, str]:
    title = f"Rain on a tin roof ({topic})"
    ctx.set_run_label(title, description=f"one song about {topic}")  # known only now
    return {"title": title}


@wf.step()
def lyrics(idea: dict[str, str], mood: str) -> str:
    return f"{idea['title']}, sung {mood}"


run = wf.run([fk.Item("01", {"mood": "softly"})], params={"topic": "rain"}, label="Rain song draft")
print(run.manifest["label"], "-", run.manifest["description"])
assert run.manifest["label"] == "Rain on a tin roof (rain)"  # the step renamed it

run.set_label("Rain song (final)")  # later, from any process; the description is kept
forked = run.fork(refresh=("lyrics",))

for summary in fk.open_runs(storage, "songs").runs():
    print(summary.run_id, summary.label, "|", summary.description)
labels = [s.label for s in fk.open_runs(storage, "songs").runs()]
assert labels == ["Rain song (final) (fork)", "Rain song (final)"]
