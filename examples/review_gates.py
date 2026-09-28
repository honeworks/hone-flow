"""Review gates: a person approves, edits or rejects a step's output, and the run continues later.

What: a ``@wf.gate()`` pauses each item after the step it reviews (its *producer*). The run ends
``awaiting_review`` and the process may exit. Later, maybe in another process, someone decides per item:
``approve`` (continue as is), ``edit`` (continue with a corrected value) or ``reject`` with a note (the
producer reruns and receives the note as ``review_note``, then the gate pauses again).

How: ``run = wf.open_run(run_id)``; ``run.approve(step=<gate>, item=...)``,
``run.edit(step=<gate>, item=..., value=...)`` or ``run.reject(step=<gate>, item=..., note=...)``; then
``run.resume()``. A producer that declares ``review_note: str | None = None`` receives the latest note.

Why: human judgement becomes part of the record instead of a side channel: every decision is kept in
the gate's ``metadata.json`` (``reviews``: decision, actor, note, time) and as a ``hone.flow.gate`` span,
and rejected attempts stay under ``attempts/`` so you can see what was sent back and why.
"""

import tempfile
from typing import Any

import hone_flow as fk

wf = fk.Workflow("review_demo", storage=tempfile.mkdtemp())


@wf.step(deterministic=False)
def shotlist(line: str, ctx: fk.Context, review_note: str | None = None) -> dict[str, Any]:
    lighting = "dark" if review_note and "dark" in review_note else "bright"
    return {"shots": [f"{line}, {lighting} light"], "attempt": ctx.attempt, "note": review_note}


@wf.gate()
def review_shotlist(shotlist: dict[str, Any]) -> dict[str, Any]:
    return shotlist  # a gate usually passes its input through for a person to judge


@wf.step()
def render(review_shotlist: dict[str, Any]) -> str:
    return " | ".join(review_shotlist["shots"])


items = [
    fk.Item("01", {"line": "city at night"}),
    fk.Item("02", {"line": "empty beach"}),
    fk.Item("03", {"line": "rooftop"}),
]
run = wf.run(items)
print(run.status)  # awaiting_review: the process could exit here
assert run.status == "awaiting_review"

run = wf.open_run(run.run_id)  # e.g. the next morning, in a review tool
run.approve(step="review_shotlist", item="01", note="good", actor="ana")
run.edit(step="review_shotlist", item="02", value={"shots": ["empty beach, drone shot"]}, actor="ana")
run.reject(step="review_shotlist", item="03", note="make it dark and moody", actor="ana")
run.resume()  # 01 and 02 render; 03's shotlist reruns with the note; its gate pauses again
revised = run.output("shotlist", "03")
print(run.status, "| 03 revised:", revised)
assert revised == {"shots": ["rooftop, dark light"], "attempt": 2, "note": "make it dark and moody"}
assert run.output("render", "02") == "empty beach, drone shot"

run.approve(step="review_shotlist", item="03")
run.resume()
gate = run.steps("review_shotlist", "03")[0]
print(run.status, "| 03 reviews:", [(r["decision"], r["actor"], r["note"]) for r in gate.reviews])
assert run.status == "completed"
assert [r["decision"] for r in gate.reviews] == ["rejected", "approved"]
assert run.steps("review_shotlist", "02")[0].labels == ["edited"]
assert run.steps("shotlist", "03")[0].attempts[0]["status"] == "rejected"
