"""AC-32: items from a step's output (dynamic fan-out, design change 0005)."""

from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk
from hone_flow.testing import FakeGpuLease

pytestmark = pytest.mark.e2e


def explainer(storage: Any, calls: list[str], **options: Any) -> fk.Workflow:
    wf = fk.Workflow("explainer", storage=storage, **options)

    @wf.global_step()
    def outline(concept: fk.Param[str], chapters: fk.Param[list[str]]) -> fk.Items:
        calls.append("outline")
        if concept == "boom":
            raise ValueError("no outline")
        return fk.Items(
            fk.Item(f"ch{n}", {"title": t, "concept": concept}) for n, t in enumerate(chapters, 1)
        )

    @wf.step(per="outline")
    def narrate(title: str, concept: str, ctx: fk.Context) -> str:
        calls.append(f"narrate/{ctx.item_id}")
        return f"{concept}: {title}"

    @wf.step(per="outline", resources="gpu:comfy")
    def picture(narrate: str, ctx: fk.Context) -> str:
        calls.append(f"picture/{ctx.item_id}")
        return f"[image of {narrate}]"

    @wf.final_step()
    def edit(picture: dict[str, str], narrate: dict[str, str]) -> list[str]:
        calls.append("edit")
        return [f"{narrate[ch]} {picture[ch]}" for ch in picture]

    return wf


PARAMS = {"concept": "tides", "chapters": ["moon", "sun", "shore"]}


@pytest.mark.parametrize("order", ["breadth_first", "depth_first"])
def test_ac32_a_step_makes_the_items(tmp_path: Path, order: str) -> None:
    calls: list[str] = []
    gpu = FakeGpuLease()
    run = explainer(tmp_path / "flows", calls, order=order, gpu=gpu).run([], params=PARAMS)
    assert run.status == "completed"
    items = run.manifest["items"]
    assert [(i["id"], i["items_from"]) for i in items] == [
        ("ch1", "outline"),
        ("ch2", "outline"),
        ("ch3", "outline"),
    ]
    assert run.output("outline") == fk.Items(
        [fk.Item(f"ch{n}", {"title": t, "concept": "tides"}) for n, t in enumerate(PARAMS["chapters"], 1)]
    )
    assert run.output("narrate", "ch2") == "tides: sun"
    assert run.output("edit")[0] == "tides: moon [image of tides: moon]"
    assert Path(run.location, "narrate/item_ch3/inputs/title.json").is_file()
    assert calls[0] == "outline" and calls[-1] == "edit"
    if order == "breadth_first":
        assert calls[1:4] == ["narrate/ch1", "narrate/ch2", "narrate/ch3"]
        assert [c for c in gpu.calls if c[0] == "enter"] == [("enter", "gpu:comfy", 0.0)]  # one batch
    else:
        assert calls[1:3] == ["narrate/ch1", "picture/ch1"]
    step_table = {s["name"]: s["per"] for s in run.manifest["steps"]}
    assert step_table == {"outline": None, "narrate": "outline", "picture": "outline", "edit": None}
    assert [(r.step, r.item) for r in run.steps("narrate")] == [("narrate", f"ch{n}") for n in (1, 2, 3)]


def test_ac32_resume_after_the_producer_failed(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = explainer(tmp_path / "flows", calls)
    run = wf.run([], params=PARAMS | {"concept": "boom"})
    assert run.status == "failed"
    assert run.manifest["items"] == []  # nothing produced yet
    assert run.manifest["state"] == {"outline": "failed", "edit": "blocked"}
    with pytest.raises(fk.WorkflowDefinitionError, match="unknown items"):
        wf.open_run(run.run_id).resume(items=["ch1"])


def test_ac32_partial_resume_of_produced_items(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = explainer(tmp_path / "flows", calls)
    run = wf.run([], params=PARAMS, until="narrate")
    assert run.status == "partial"
    assert run.manifest["state"]["picture/ch1"] == "skipped"
    calls.clear()
    wf.open_run(run.run_id).resume(items=["ch2"])  # produced ids can be named once they exist
    assert calls == ["picture/ch2"]
    wf.open_run(run.run_id).resume()
    assert run.status == "completed"


def test_ac32_fork_reruns_only_changed_items(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = explainer(tmp_path / "flows", calls)
    run = wf.run([], params=PARAMS)
    calls.clear()

    changed = run.fork(params={"chapters": ["moon", "stars", "shore", "wind"]})
    assert changed.status == "completed"
    assert calls == [
        "outline",
        "narrate/ch2",
        "narrate/ch4",
        "picture/ch2",
        "picture/ch4",
        "edit",
    ]
    assert changed.steps("narrate", "ch1")[0].labels == ["reused"]
    plan = {(r["step"], r["item"]): (r["action"], r["reason"]) for r in changed.manifest["fork_of"]["plan"]}
    assert plan[("outline", None)] == ("run", "param_changed:chapters")
    assert plan[("narrate", "ch1")] == ("reuse", "unchanged")
    assert plan[("narrate", "ch2")] == ("run", "produced_item_changed")
    assert plan[("narrate", "ch4")] == ("run", "new_item")
    assert plan[("edit", None)][0] == "run"
    assert len(changed.output("edit")) == 4

    calls.clear()
    shorter = run.fork(params={"chapters": ["moon", "sun"]})
    assert calls == ["outline", "edit"]  # ch3 is gone; ch1 and ch2 are copied
    assert [i["id"] for i in shorter.manifest["items"]] == ["ch1", "ch2"]
    assert "narrate/ch3" not in shorter.manifest["state"]
    assert not Path(shorter.location, "narrate/item_ch3").exists()
    assert ("narrate", "ch3") not in {(r["step"], r["item"]) for r in shorter.manifest["fork_of"]["plan"]}


def test_ac32_bad_producers_and_definitions(tmp_path: Path) -> None:
    wf = fk.Workflow("bad", storage=tmp_path / "flows")

    produced = {
        "clash": [fk.Item("given")],
        "file": [fk.Item("p1", {"text": fk.File(__file__)})],
        "missing": [fk.Item("p1", {})],
        "ok": [fk.Item("p1", {"text": "x"})],
    }

    @wf.global_step()
    def parts(mode: fk.Param[str]) -> fk.Items:
        if mode == "list":
            return [fk.Item("p1", {"text": "x"})]  # type: ignore[return-value]
        return fk.Items(produced[mode])

    @wf.step(per="parts")
    def use(text: str) -> str:
        return text

    @wf.step()
    def own(n: int) -> int:
        return n

    for mode, message in [
        ("clash", "already items of the run"),
        ("file", "produced items take JSON values"),
        ("missing", "parameter 'text' is not a step output"),
        ("list", "annotated to return fk.Items; it returned list"),
    ]:
        failed = wf.run([fk.Item("given", {"n": 1})], params={"mode": mode})
        assert failed.status == "failed", mode
        assert message in failed.steps("parts")[0].error["traceback"], mode  # type: ignore[index]
    assert wf.run([fk.Item("given", {"n": 1})], params={"mode": "ok"}).status == "completed"


def test_ac32_definition_errors(tmp_path: Path) -> None:
    wrong = fk.Workflow("wrong", storage=tmp_path / "flows")

    @wrong.global_step()
    def plain(ctx: fk.Context) -> list[str]:
        return []

    @wrong.step(per="plain")
    def uses(text: str) -> str:
        return text

    with pytest.raises(fk.WorkflowDefinitionError, match=r"must name a @wf\.global_step annotated"):
        wrong.validate()

    mixed = fk.Workflow("mixed", storage=tmp_path / "flows")

    @mixed.global_step()
    def chapters(ctx: fk.Context) -> fk.Items:
        return fk.Items()

    @mixed.step()
    def lesson(n: int) -> int:
        return n

    @mixed.step(per="chapters")
    def scene(lesson: int) -> int:
        return lesson

    with pytest.raises(fk.WorkflowDefinitionError, match="the items differ"):
        mixed.validate()
