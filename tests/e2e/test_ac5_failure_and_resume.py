"""AC-5: failure isolation, `blocked` steps, `fail_fast`, and resume after a fix (attempts)."""

from pathlib import Path

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e


def failing_flow(storage: Path, calls: list[tuple[str, str]], **options: object) -> fk.Workflow:
    wf = fk.Workflow("fragile", storage=storage, **options)  # type: ignore[arg-type]

    @wf.step()
    def parse(item: fk.Item, text: str) -> str:
        calls.append(("parse", item.id))
        if item.id == "02":
            raise ValueError(f"cannot parse {text!r}")
        return text.upper()

    @wf.step()
    def publish(parse: str, ctx: fk.Context) -> str:
        calls.append(("publish", ctx.item_id))
        return parse + "!"

    return wf


def test_ac5_failure_and_resume_isolates_failures(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = failing_flow(tmp_path, calls)
    items = [fk.Item("01", {"text": "ok"}), fk.Item("02", {"text": "bad"}), fk.Item("03", {"text": "fine"})]
    run = wf.run(items)
    assert run.status == "failed"
    assert run.output("publish", "01") == "OK!"
    assert run.output("publish", "03") == "FINE!"  # other items continue after 02 failed
    failed = run.steps("parse", "02")[0]
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error["message"] == "ValueError: cannot parse 'bad'"
    assert "Traceback (most recent call last)" in failed.error["traceback"]
    assert run.steps("publish", "02")[0].status == "blocked"
    assert run.manifest["state"]["publish/02"] == "blocked"
    assert run.manifest["state"]["parse/02"] == "failed"
    (span,) = [
        s
        for s in run.spans()
        if s["attributes"].get("hone.item") == "02" and s["attributes"]["hone.step"] == "parse"
    ]
    assert span["status"] == {"code": "error", "message": "ValueError: cannot parse 'bad'"}
    assert span["events"][0]["name"] == "exception"
    assert ("publish", "02") not in calls
    folder = Path(run.location)
    assert not (folder / "parse/item_02/output").exists()  # output/ only ever holds a success
    assert not (folder / "publish/item_02").exists()  # a blocked step has no folder
    with pytest.raises(fk.OutputNotFound):
        run.output("parse", "02")


def test_ac5_failure_and_resume_failed_attempt_keeps_its_files(tmp_path: Path) -> None:
    wf = fk.Workflow("scratch", storage=tmp_path)

    @wf.step()
    def render(ctx: fk.Context) -> str:
        ctx.new_file("partial.txt").write_text("half a video")
        raise RuntimeError("encoder crashed")

    run = wf.run([fk.Item("01")])
    folder = Path(run.location)
    assert (folder / "render/item_01/attempts/1/output/partial.txt").read_text() == "half a video"
    assert run.steps("render", "01")[0].attempt == 1


def test_ac5_failure_and_resume_fail_fast(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = failing_flow(tmp_path, calls, fail_fast=True)
    items = [fk.Item("01", {"text": "ok"}), fk.Item("02", {"text": "bad"}), fk.Item("03", {"text": "fine"})]
    with pytest.raises(fk.StepFailed) as raised:
        wf.run(items)
    assert raised.value.step == "parse"
    assert raised.value.item == "02"
    assert "cannot parse" in raised.value.traceback
    assert ("parse", "03") not in calls  # stopped at the first failure
    (run_id,) = [p.name for p in (tmp_path / "fragile" / "runs").iterdir()]
    run = wf.open_run(run_id)
    assert raised.value.run_id == run_id
    assert run.status == "failed"
    assert run.steps("parse", "02")[0].status == "failed"  # committed before raising
    assert run.manifest["state"]["parse/03"] == "pending"
    assert "running" not in run.manifest["state"].values()
    assert not (tmp_path / "fragile" / "runs" / run_id / "lease.json").exists()


def test_ac5_failure_and_resume_failed_global_step_blocks_items(tmp_path: Path) -> None:
    wf = fk.Workflow("globals", storage=tmp_path)

    @wf.global_step()
    def style() -> str:
        raise RuntimeError("no style")

    @wf.step()
    def draft(text: str) -> str:
        return text

    @wf.step()
    def styled(draft: str, style: str) -> str:
        return draft + style

    run = wf.run([fk.Item("01", {"text": "a"}), fk.Item("02", {"text": "b"})])
    assert run.status == "failed"
    assert run.steps("style")[0].status == "failed"
    assert [r.status for r in run.steps("draft")] == ["done", "done"]  # does not use it
    assert [r.status for r in run.steps("styled")] == ["blocked", "blocked"]


def test_ac5_failure_and_resume_interrupt_marks_the_run_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HONE_FLOW_WORKDIR", str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    wf = fk.Workflow("ctrl_c", storage=tmp_path / "flows")

    @wf.step()
    def slow(ctx: fk.Context) -> str:
        ctx.new_file("half.txt").write_text("...")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        wf.run([fk.Item("01")])
    (folder,) = (tmp_path / "flows" / "ctrl_c" / "runs").iterdir()
    run = wf.open_run(folder.name)
    assert run.status == "interrupted"
    assert run.manifest["state"]["slow/01"] == "interrupted"
    assert not (folder / "lease.json").exists()
    assert not (folder / "slow/item_01/output").exists()
    assert list((tmp_path / "work").iterdir()) == []  # the local work folder is removed


def test_ac5_failure_and_resume_reruns_only_failed_and_blocked(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    seeds: dict[int, int] = {}
    wf = fk.Workflow("fixable", storage=tmp_path)
    broken = {"02": True}

    @wf.step()
    def parse(item: fk.Item, text: str, ctx: fk.Context) -> str:
        calls.append(("parse", item.id))
        if item.id == "02":
            seeds[ctx.attempt] = ctx.seed
            if broken["02"]:
                raise ValueError("bug")
        return text.upper()

    @wf.step()
    def publish(parse: str, ctx: fk.Context) -> str:
        calls.append(("publish", ctx.item_id))
        return parse + "!"

    items = [fk.Item("01", {"text": "ok"}), fk.Item("02", {"text": "bad"})]
    run = wf.run(items)
    assert run.status == "failed"
    broken["02"] = False  # "fix the code" (same version)
    calls.clear()
    assert run.resume() is run
    assert calls == [("parse", "02"), ("publish", "02")]  # only 02's failed and blocked steps
    assert run.status == "completed"
    record = run.steps("parse", "02")[0]
    assert record.attempt == 2
    assert [(a["attempt"], a["status"]) for a in record.attempts] == [(1, "failed")]
    assert "ValueError: bug" in record.attempts[0]["error"]["traceback"]
    assert record.error is None
    assert seeds[1] == seeds[2]  # a retry keeps the seed
    assert run.output("publish", "02") == "BAD!"
    assert [c["kind"] for c in run.manifest["calls"]] == ["run", "resume"]
    run_spans = [s for s in run.spans() if s["name"] == "hone.flow.run"]
    assert len({s["trace_id"] for s in run_spans}) == 1  # resume continues the trace
    assert run.resume().status == "completed"  # nothing left to do
    assert calls == [("parse", "02"), ("publish", "02")]


def test_ac5_failure_and_resume_detached_and_unknown_items(tmp_path: Path) -> None:
    _, run = tmp_path, fk.Workflow("x", storage=tmp_path)

    @run.step()
    def one(text: str) -> str:
        return text

    started = run.run([fk.Item("01", {"text": "a"})])
    with pytest.raises(fk.WorkflowDefinitionError, match=r"unknown items \['09'\]"):
        started.resume(items=["09"])
    with pytest.raises(fk.WorkflowDefinitionError, match=r"unknown items \['09'\]"):
        run.run([fk.Item("01", {"text": "a"})], items_filter=["09"])
    detached = fk.Run(run.storage, f"x/runs/{started.run_id}")
    with pytest.raises(fk.HoneFlowError, match=r"resume needs the workflow's code: use wf\.open_run"):
        detached.resume()
