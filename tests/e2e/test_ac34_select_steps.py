"""AC-34: choosing which items continue mid-run (design change 0007): select steps and not_selected."""

from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e


def leads(storage: Any, calls: list[str], **options: Any) -> fk.Workflow:
    wf = fk.Workflow("leads", storage=storage, **options)

    @wf.step()
    def fit(company: str, score: int, ctx: fk.Context) -> dict[str, Any]:
        calls.append(f"fit/{ctx.item_id}")
        if score < 0:
            raise ValueError("unreadable post")
        return {"company": company, "score": score}

    @wf.select_step(partial_ok=True)
    def shortlist(fit: dict[str, dict[str, Any]], top: fk.Param[int]) -> fk.Selection:
        calls.append("shortlist")
        best: dict[str, str] = {}
        for item, row in sorted(fit.items(), key=lambda kv: -kv[1]["score"]):
            best.setdefault(row["company"], item)
        keep = sorted(best.values(), key=lambda i: -fit[i]["score"])[:top]
        reasons = {i: "lower score" if i in best.values() else "same company" for i in fit if i not in keep}
        return fk.Selection(keep=keep, reasons=reasons)

    @wf.step()
    def draft(fit: dict[str, Any], shortlist: fk.Selection, ctx: fk.Context) -> str:
        calls.append(f"draft/{ctx.item_id}")
        return f"Hello {fit['company']}"

    @wf.step()
    def review(draft: str) -> str:
        return draft + "!"

    @wf.final_step()
    def digest(review: dict[str, str]) -> list[str]:
        calls.append("digest")
        return sorted(review)

    return wf


POSTS = [
    ("p1", "acme", 5),
    ("p2", "acme", 9),
    ("p3", "globex", 7),
    ("p4", "initech", 3),
    ("p5", "umbrella", 8),
]


def posts(extra: list[tuple[str, str, int]] | None = None) -> list[fk.Item]:
    return [fk.Item(i, {"company": c, "score": s}) for i, c, s in [*POSTS, *(extra or [])]]


@pytest.mark.parametrize("order", ["breadth_first", "depth_first"])
def test_ac34_select_step_chooses_the_items_that_continue(tmp_path: Path, order: str) -> None:
    calls: list[str] = []
    run = leads(tmp_path / "flows", calls, order=order).run(posts(), params={"top": 2})
    assert run.status == "completed"  # not_selected counts as done
    assert run.output("shortlist") == fk.Selection(
        keep=["p2", "p5"],
        reasons={"p1": "same company", "p3": "lower score", "p4": "lower score"},
    )
    assert sorted(c for c in calls if c.startswith("draft")) == ["draft/p2", "draft/p5"]
    state = run.manifest["state"]
    assert state["draft/p1"] == "not_selected"
    assert state["review/p3"] == "not_selected"  # downstream of a not-selected step too
    assert state["fit/p1"] == "done"
    assert run.output("digest") == ["p2", "p5"]  # a final step sees only the selected items
    assert calls.index("shortlist") > max(calls.index(f"fit/{i}") for i, _, _ in POSTS)
    (record,) = run.steps("draft", "p4")
    assert (record.status, record.attempt) == ("not_selected", 0)
    assert run.steps("shortlist")[0].kind == "select"
    spans = [s for s in run.spans() if s["attributes"].get("hone.flow.status") == "not_selected"]
    assert len(spans) == 6  # draft and review for p1, p3, p4

    with pytest.raises(fk.HoneFlowError, match=r"items \['p1'\] were not selected"):
        leads(tmp_path / "flows", calls, order=order).open_run(run.run_id).resume(items=["p1"])


def test_ac34_failures_partial_ok_and_fork(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = leads(tmp_path / "flows", calls)
    broken = wf.run(posts([("p6", "hooli", -1)]), params={"top": 3})
    assert broken.status == "failed"  # p6's fit failed; partial_ok let the selection go on without it
    assert broken.output("shortlist").keep == ["p2", "p5", "p3"]
    assert broken.manifest["state"]["draft/p6"] == "blocked"  # a failure upstream wins over not_selected

    run = wf.run(posts(), params={"top": 3})
    calls.clear()
    same = run.fork(dry_run=True)
    rows = {(r.step, r.item): (r.action, r.reason) for r in same.rows}
    assert rows[("draft", "p1")] == ("reuse", "not_selected")
    assert rows[("shortlist", None)] == ("reuse", "unchanged")
    copied = run.fork()
    assert calls == []
    assert copied.manifest["state"]["draft/p1"] == "not_selected"

    fewer = run.fork(items=["p1", "p2", "p3", "p4"])
    assert fewer.status == "completed"
    # the selection is made again (items_changed); the steps that take it rerun for the kept items
    assert calls == ["shortlist", "draft/p2", "draft/p3", "draft/p4", "digest"]
    reasons = {(r["step"], r["item"]): r["reason"] for r in fewer.manifest["fork_of"]["plan"]}
    assert reasons[("shortlist", None)] == "items_changed"
    assert fewer.output("shortlist").keep == ["p2", "p3", "p4"]

    calls.clear()
    wider = run.fork(params={"top": 4})
    assert wider.output("shortlist").keep == ["p2", "p5", "p3", "p4"]
    assert calls == ["shortlist", "draft/p2", "draft/p3", "draft/p4", "draft/p5", "digest"]  # item order
    assert wider.manifest["state"]["draft/p1"] == "not_selected"


def test_ac34_select_rejects_unknown_ids_and_plain_lists(tmp_path: Path) -> None:
    wf = fk.Workflow("pick", storage=tmp_path / "flows")

    @wf.step()
    def score(n: int) -> int:
        return n

    @wf.select_step()
    def pick(score: dict[str, int], mode: fk.Param[str]) -> list[str]:
        return ["zz"] if mode == "bad" else [k for k, v in score.items() if v > 1]

    @wf.step()
    def use(score: int, pick: fk.Selection) -> int:
        return score * 10

    items = [fk.Item("a", {"n": 1}), fk.Item("b", {"n": 2})]
    good = wf.run(items, params={"mode": "ok"})
    assert good.output("pick") == fk.Selection(keep=["b"])  # a plain list is stored as a Selection
    assert good.output("use", "b") == 20
    bad = wf.run(items, params={"mode": "bad"})
    assert bad.status == "failed"
    (record,) = bad.steps("pick")
    assert "kept unknown items ['zz']" in record.error["message"]  # type: ignore[index]
    assert bad.manifest["state"]["use/a"] == "blocked"


def test_ac34_rejecting_before_the_selection_selects_again(tmp_path: Path) -> None:
    wf = fk.Workflow("again", storage=tmp_path / "flows")

    @wf.step()
    def score(n: int, review_note: str | None = None) -> int:
        return n * 10 if review_note else n

    @wf.gate()
    def check(score: int) -> int:
        return score

    @wf.select_step()
    def best(check: dict[str, int]) -> list[str]:
        return [max(check, key=lambda k: check[k])]

    @wf.step()
    def post(check: int, best: fk.Selection) -> str:
        return f"posted {check}"

    run = wf.run([fk.Item("a", {"n": 1}), fk.Item("b", {"n": 2})])
    for item in ("a", "b"):
        run.approve(step="check", item=item)
    run.resume()
    assert run.output("best").keep == ["b"]
    assert run.manifest["state"]["post/a"] == "not_selected"

    fresh = wf.open_run(run.run_id).fork(refresh={"score": ["a"]})
    fresh.reject(step="check", item="a", note="scale it")
    assert fresh.manifest["state"]["post/a"] == "pending"  # the selection will be made again
    wf.open_run(fresh.run_id).resume()
    fresh.approve(step="check", item="a")
    wf.open_run(fresh.run_id).resume()
    assert fresh.output("best").keep == ["a"]
    assert fresh.output("post", "a") == "posted 10"
