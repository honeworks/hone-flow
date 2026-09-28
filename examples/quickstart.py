"""Quickstart: two steps, two items, one run folder.

What: the smallest useful workflow. Two plain functions become steps; hone-flow runs them for every item
and keeps the run as a folder you can open.

How: create ``fk.Workflow(name, storage=...)``; decorate functions with ``@wf.step()``; name a
parameter after another step to receive its output (``words``), after an item input to receive that
input (``text``), or annotate it ``fk.Param[...]`` to receive a run-level param (``mark``). Then
``wf.run(items, params=...)`` executes the run and ``run.output(step, item)`` reads a result back.

Why: steps stay ordinary functions you can call and test on their own; the wiring comes from parameter
names, so there is no second list of steps to keep in sync. Every run is written to
``<storage>/<name>/runs/<run_id>/``, so nothing is lost when the process ends.
"""

import tempfile

import hone_flow as fk

wf = fk.Workflow("hello", storage=tempfile.mkdtemp())


@wf.step()
def words(text: str) -> list[str]:  # `text` is an item input
    return text.split()


@wf.step()
def shout(words: list[str], mark: fk.Param[str]) -> str:  # `words` is the output of step `words`
    return " ".join(w.upper() for w in words) + mark


run = wf.run(
    [fk.Item("a", {"text": "hello world"}), fk.Item("b", {"text": "good night"})], params={"mark": "!"}
)
print(run.status, run.output("shout", "a"))
print("run folder:", run.location)
assert run.status == "completed"
assert run.output("shout", "b") == "GOOD NIGHT!"
assert shout(["plain", "call"], "?") == "PLAIN CALL?"  # still a normal function
