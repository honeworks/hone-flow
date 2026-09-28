"""Select steps: choose which items continue, in the middle of a run.

What: a ``@wf.select_step`` sees every item's output (like a final step) and returns the ids that go on.
Item steps that take its output run only for those items; for the others they become ``not_selected``,
a final state that counts as done, so the run ends ``completed``.

How: score every item, then decide in a select step that returns a list of ids or
``fk.Selection(keep=[...], reasons={id: why})``. Steps after the choice name the select step as a
parameter (they receive the ``fk.Selection``). ``partial_ok=True`` lets it choose among the items that
are done when some failed.

Why: a daily shortlist (45 posts scored, 10 drafted) is the most important decision of the run. Made in
app code between two calls, it is outside the run folder, the run stays ``partial`` for ever and a bare
``resume()`` would draft all 45. As a step it is recorded, snapshotted and forked like any other.
"""

import tempfile

import hone_flow as fk

calls: list[str] = []
wf = fk.Workflow("leads", storage=tempfile.mkdtemp())


@wf.step()
def fit(company: str, score: int) -> dict[str, object]:
    return {"company": company, "score": score}


@wf.select_step()
def shortlist(fit: dict[str, dict[str, object]], top: fk.Param[int]) -> fk.Selection:
    ranked = sorted(fit, key=lambda post: -int(str(fit[post]["score"])))
    best_per_company: dict[object, str] = {}
    for post in ranked:
        best_per_company.setdefault(fit[post]["company"], post)
    keep = [post for post in ranked if post in best_per_company.values()][:top]
    why = {post: "same company" if post not in best_per_company.values() else "below the top" for post in fit}
    return fk.Selection(keep=keep, reasons={post: why[post] for post in fit if post not in keep})


@wf.step()
def draft(fit: dict[str, object], shortlist: fk.Selection, item: fk.Item) -> str:
    calls.append(item.id)
    return f"Hello {fit['company']}, ..."


posts = [("p1", "acme", 5), ("p2", "acme", 9), ("p3", "globex", 7), ("p4", "initech", 3)]
run = wf.run([fk.Item(p, {"company": c, "score": s}) for p, c, s in posts], params={"top": 2})
choice = run.output("shortlist")
print("kept:", choice.keep, "left out:", choice.reasons)
print({key: state for key, state in run.manifest["state"].items() if key.startswith("draft")})

assert run.status == "completed"
assert choice.keep == ["p2", "p3"]
assert calls == ["p2", "p3"]
assert run.manifest["state"]["draft/p1"] == "not_selected"
