"""Partial runs: run part of the workflow now, the rest later.

What: ``wf.run(..., until="step")`` runs that step and what it needs; ``items_filter=[...]`` runs only
some items. Everything left out is ``skipped`` and the run is ``partial``. ``run.resume(until=...,
items=...)`` continues the same run, selectively or completely.

How: pass ``until`` / ``items_filter`` to ``wf.run`` and ``until`` / ``items`` to ``run.resume``.
Inside the selection the usual rules apply; outside it nothing runs.

Why: try the cheap steps on every item before paying for the expensive ones, or look at one item end to
end before running the other hundred, without a hand-written ``--from/--until`` runner.
"""

import tempfile

import hone_flow as fk

calls: list[str] = []
wf = fk.Workflow("partial_demo", storage=tempfile.mkdtemp())


@wf.step()
def outline(topic: str, ctx: fk.Context) -> str:
    calls.append(f"outline/{ctx.item_id}")
    return f"outline of {topic}"


@wf.step(resources="gpu:ollama")
def article(outline: str, ctx: fk.Context) -> str:  # the expensive step
    calls.append(f"article/{ctx.item_id}")
    return outline.replace("outline", "article")


items = [
    fk.Item("01", {"topic": "tides"}),
    fk.Item("02", {"topic": "moons"}),
    fk.Item("03", {"topic": "rain"}),
]
run = wf.run(items, until="outline")  # only the cheap step, for every item
print(run.status, calls)
assert run.status == "partial"
assert run.steps("article", "01")[0].status == "skipped"

calls.clear()
run.resume(items=["02"])  # one item end to end
print(run.status, calls)
assert calls == ["article/02"]

calls.clear()
run.resume()  # the rest
print(run.status, calls)
assert calls == ["article/01", "article/03"]
assert run.status == "completed"
