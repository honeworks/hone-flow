"""AC-31: steps over all items (fan-in, design change 0004) and per-item refresh in a fork."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import hone_flow as fk
from hone_flow.cli import app

pytestmark = pytest.mark.e2e


def course(storage: Any, calls: list[str], fail: set[str] | None = None, **options: Any) -> fk.Workflow:
    wf = fk.Workflow("course", storage=storage, **options)
    broken = fail or set()

    @wf.global_step()
    def curriculum(title: fk.Param[str]) -> dict[str, str]:
        calls.append("curriculum")
        return {"title": title}

    @wf.step()
    def script(topic: str, curriculum: dict[str, str], ctx: fk.Context, note: str = "") -> str:
        calls.append(f"script/{ctx.item_id}")
        if ctx.item_id in broken:
            raise ValueError("no script")
        return f"{curriculum['title']}: {topic}{note}"

    @wf.step()
    def summary(script: str, ctx: fk.Context) -> dict[str, Any]:
        calls.append(f"summary/{ctx.item_id}")
        return {"text": script.upper(), "words": len(script.split())}

    @wf.final_step()
    def workbook(summary: dict[str, dict[str, Any]], curriculum: dict[str, str], ctx: fk.Context) -> fk.File:
        calls.append("workbook")
        out = ctx.new_file("workbook.md")
        out.write_text("\n".join([curriculum["title"], *(f"{k}: {v['text']}" for k, v in summary.items())]))
        return fk.File(out)

    @wf.final_step()
    def course_page(workbook: fk.File, script: dict[str, str]) -> dict[str, Any]:
        calls.append("course_page")
        return {"lessons": list(script), "workbook_bytes": len(workbook.path.read_bytes())}

    return wf


def lessons() -> list[fk.Item]:
    return [
        fk.Item(i, {"topic": t}) for i, t in [("01", "listening"), ("02", "feedback"), ("03", "conflict")]
    ]


@pytest.mark.parametrize("order", ["breadth_first", "depth_first"])
def test_ac31_final_steps_see_every_item(tmp_path: Path, order: str) -> None:
    calls: list[str] = []
    wf = course(tmp_path / "flows", calls, order=order)
    run = wf.run(lessons(), params={"title": "Soft skills"})
    assert run.status == "completed"
    assert calls.index("workbook") > max(calls.index(f"summary/{i}") for i in ("01", "02", "03"))
    assert calls[-2:] == ["workbook", "course_page"]
    book = run.output("workbook")
    assert book.path.read_text().splitlines() == [
        "Soft skills",
        "01: SOFT SKILLS: LISTENING",
        "02: SOFT SKILLS: FEEDBACK",
        "03: SOFT SKILLS: CONFLICT",
    ]
    size = len(book.path.read_bytes())
    assert run.output("course_page") == {"lessons": ["01", "02", "03"], "workbook_bytes": size}

    # stored like a global step, with a snapshot of every item's input
    (record,) = run.steps("workbook")
    assert (record.kind, record.item, record.status) == ("final", None, "done")
    assert record.inputs["summary"]["from"] == "items:summary"
    assert sorted(record.inputs["summary"]["files"]) == [
        f"summary/{i}/summary.json" for i in ("01", "02", "03")
    ]
    assert Path(run.location, "workbook/inputs/summary/02/summary.json").is_file()
    assert record.inputs["curriculum"]["from"] == "global:curriculum"
    step_kinds = {s["name"]: s["kind"] for s in run.manifest["steps"]}
    assert step_kinds["workbook"] == "final"
    assert run.manifest["state"]["workbook"] == "done"


def test_ac31_blocked_skipped_and_partial_ok(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = course(tmp_path / "flows", calls, fail={"02"})
    run = wf.run(lessons(), params={"title": "T"})
    assert run.status == "failed"
    assert run.manifest["state"]["workbook"] == "blocked"  # an item it needs failed
    assert run.manifest["state"]["course_page"] == "blocked"

    fixed = course(tmp_path / "flows", calls)
    partial = fixed.run(lessons(), params={"title": "T"}, items_filter=["01"])
    assert partial.status == "partial"
    assert partial.manifest["state"]["workbook"] == "skipped"  # waits for the items left out
    fixed.open_run(partial.run_id).resume(items=["02"])
    assert partial.manifest["state"]["workbook"] == "skipped"
    fixed.open_run(partial.run_id).resume()
    assert partial.status == "completed"
    assert len(partial.output("course_page")["lessons"]) == 3

    lenient = fk.Workflow("lenient", storage=tmp_path / "flows")

    @lenient.step()
    def grade(score: int) -> int:
        if score < 0:
            raise ValueError("bad score")
        return score

    @lenient.final_step(partial_ok=True)
    def average(grade: dict[str, int]) -> float:
        return sum(grade.values()) / len(grade)

    graded = lenient.run(
        [fk.Item("a", {"score": 4}), fk.Item("b", {"score": -1}), fk.Item("c", {"score": 8})]
    )
    assert graded.status == "failed"  # the failed item still fails the run
    assert graded.output("average") == 6.0  # computed from the done items


def test_ac31_fork_reruns_the_final_steps_of_a_refreshed_item(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = course(tmp_path / "flows", calls)
    run = wf.run(lessons(), params={"title": "T"})
    calls.clear()

    plan = run.fork(refresh={"script": ["02"]}, dry_run=True)
    rows = {(r.step, r.item): (r.action, r.reason) for r in plan.rows}
    assert rows[("script", "02")] == ("run", "refresh_requested")
    assert rows[("script", "01")] == ("reuse", "unchanged")
    assert rows[("summary", "02")] == ("run", "downstream_of:script")
    assert rows[("workbook", None)] == ("run", "downstream_of:script")
    assert rows[("course_page", None)] == ("run", "downstream_of:script")
    assert rows[("curriculum", None)] == ("reuse", "unchanged")

    forked = run.fork(refresh={"script": ["02"]})
    assert calls == ["script/02", "summary/02", "workbook", "course_page"]
    assert forked.manifest["fork_of"]["plan"] == [
        {"item": r.item, "step": r.step, "action": r.action, "reason": r.reason} for r in plan.rows
    ]
    assert [r.labels for r in forked.steps("summary", "01")] == [["reused"]]

    fewer = run.fork(items=["01", "03"], dry_run=True)
    reasons = {(r.step, r.item): r.reason for r in fewer.rows}
    assert reasons[("workbook", None)] == "items_changed"
    assert reasons[("summary", "01")] == "unchanged"

    with pytest.raises(fk.WorkflowDefinitionError, match="unknown items"):
        run.fork(refresh={"script": ["09"]}, dry_run=True)


def test_ac31_cli_refresh_per_item(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2]))
    (tmp_path / "course_flow.py").write_text(
        "from tests.e2e.test_ac31_final_steps import course\nwf = course('flows', [])\n"
    )
    run = course(tmp_path / "flows", []).run(lessons(), params={"title": "T"})
    result = CliRunner().invoke(
        app, ["fork", run.run_id, "--flow", "course_flow:wf", "--refresh", "script=02", "--dry-run", "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = {(r["step"], r["item"]): r["action"] for r in json.loads(result.output)["rows"]}
    assert (rows[("script", "02")], rows[("script", "01")]) == ("run", "reuse")


def test_ac31_definition_errors(tmp_path: Path) -> None:
    wf = fk.Workflow("bad", storage=tmp_path / "flows")

    @wf.step()
    def lesson(topic: str) -> str:
        return topic

    @wf.final_step()
    def book(lesson: dict[str, str], topic: str) -> str:  # an item input in a final step
        return ""

    with pytest.raises(fk.WorkflowDefinitionError, match="final step 'book' parameter 'topic'"):
        wf.validate()

    wf2 = fk.Workflow("bad2", storage=tmp_path / "flows")

    @wf2.final_step()
    def index(ctx: fk.Context) -> str:
        return "all"

    @wf2.step()
    def uses_index(index: str) -> str:  # an item step after a final step
        return index

    with pytest.raises(fk.WorkflowDefinitionError, match="final step 'index' runs after every item"):
        wf2.validate()


def test_ac31_rejecting_a_producer_replaces_the_final_step(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = fk.Workflow("reviewed", storage=tmp_path / "flows")

    @wf.step()
    def script(topic: str, ctx: fk.Context, review_note: str | None = None) -> str:
        calls.append(f"script/{ctx.item_id}")
        return f"{topic} {review_note or ''}".strip()

    @wf.gate()
    def check(script: str) -> str:
        return script

    @wf.step()
    def narrate(script: str, ctx: fk.Context) -> str:
        calls.append(f"narrate/{ctx.item_id}")
        return script.upper()

    @wf.final_step()
    def index(script: dict[str, str]) -> list[str]:
        calls.append("index")
        return sorted(script.values())

    run = wf.run([fk.Item("01", {"topic": "a"}), fk.Item("02", {"topic": "b"})])
    assert run.output("index") == ["a", "b"]
    run.reject(step="check", item="01", note="longer")
    assert run.manifest["state"]["index"] == "pending"  # it used the rejected script
    assert run.steps("index")[0].attempts[0]["status"] == "replaced"
    assert run.manifest["state"]["narrate/02"] == "done"  # the other item keeps its work
    calls.clear()
    run.resume()
    assert calls == ["script/01", "narrate/01", "index"]
    assert run.output("index") == ["a longer", "b"]
