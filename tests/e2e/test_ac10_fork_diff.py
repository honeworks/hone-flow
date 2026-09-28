"""AC-10: the automatic fork diff reruns exactly what changed, and says why."""

from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e


def make(
    storage: Path,
    calls: list[tuple[str, str]],
    *,
    shot_v: str = "1",
    wf_v: str = "1",
    shot_suffix: str = "",
    fail_render_02: bool = False,
) -> fk.Workflow:
    wf = fk.Workflow("diff", storage=storage, version=wf_v)

    @wf.step()
    def timeline(item: fk.Item, lyrics: fk.File) -> list[str]:
        calls.append(("timeline", item.id))
        return lyrics.path.read_text().splitlines()

    if shot_suffix:

        @wf.step(version=shot_v)
        def shotlist(timeline: list[str], ctx: fk.Context) -> list[str]:  # edited source
            calls.append(("shotlist", ctx.item_id))
            return [line + shot_suffix for line in timeline]
    else:

        @wf.step(version=shot_v)
        def shotlist(timeline: list[str], ctx: fk.Context) -> list[str]:
            calls.append(("shotlist", ctx.item_id))
            return timeline

    @wf.step()
    def styled(shotlist: list[str], style: fk.Param[str], ctx: fk.Context) -> list[str]:
        calls.append(("styled", ctx.item_id))
        return [f"{line} ({style})" for line in shotlist]

    @wf.step()
    def render(styled: list[str], ctx: fk.Context) -> str:
        calls.append(("render", ctx.item_id))
        if fail_render_02 and ctx.item_id == "02":
            raise RuntimeError("render crashed")
        return "\n".join(styled)

    return wf


def items(tmp_path: Path) -> list[fk.Item]:
    songs = tmp_path / "songs"
    songs.mkdir(exist_ok=True)
    (songs / "01.md").write_text("one\n")
    (songs / "02.md").write_text("two\n")
    return [
        fk.Item("01", {"lyrics": fk.File(songs / "01.md")}),
        fk.Item("02", {"lyrics": fk.File(songs / "02.md")}),
    ]


def reasons(plan: fk.ForkPlan) -> dict[tuple[str, str | None], str]:
    return {(row.step, row.item): row.reason for row in plan.rows}


def source_run(tmp_path: Path, **kwargs: Any) -> fk.Run:
    return make(tmp_path / "flows", [], **kwargs).run(items(tmp_path), params={"style": "noir"})


def fork_calls(
    tmp_path: Path, source: fk.Run, **kwargs: Any
) -> tuple[fk.ForkPlan, fk.Run, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    params = kwargs.pop("params", None)
    wf = make(tmp_path / "flows", calls, **kwargs)
    run = wf.open_run(source.run_id)
    plan = run.fork(params=params, dry_run=True)
    new = run.fork(params=params)
    runs = {(r.step, r.item) for r in plan.rows if r.action == "run"}
    assert set(calls) == runs  # the executed fork calls exactly the `run` rows
    recorded = [(r["item"], r["step"], r["action"], r["reason"]) for r in new.manifest["fork_of"]["plan"]]
    assert recorded == [(r.item, r.step, r.action, r.reason) for r in plan.rows]
    return plan, new, calls


def test_ac10_fork_diff_nothing_changed(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    plan, new, calls = fork_calls(tmp_path, source)
    assert {r.action for r in plan.rows} == {"reuse"}
    assert set(reasons(plan).values()) == {"unchanged"}
    assert calls == []
    assert new.status == "completed"


def test_ac10_fork_diff_version_bump(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    plan, _, _ = fork_calls(tmp_path, source, shot_v="2")
    why = reasons(plan)
    assert why[("shotlist", "01")] == "version_changed 1->2"
    assert why[("render", "01")] == "downstream_of:shotlist"
    assert why[("styled", "02")] == "downstream_of:shotlist"
    assert why[("timeline", "01")] == "unchanged"


def test_ac10_fork_diff_param_change(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    plan, new, _ = fork_calls(tmp_path, source, params={"style": "bright"})
    why = reasons(plan)
    assert why[("styled", "01")] == "param_changed:style"  # only steps declaring the param
    assert why[("render", "01")] == "downstream_of:styled"
    assert why[("shotlist", "01")] == "unchanged"
    assert new.output("render", "01") == "one (bright)"
    assert new.manifest["params"] == {"style": "bright"}


def test_ac10_fork_diff_edited_input_file(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    (tmp_path / "songs" / "01.md").write_text("one, rewritten\n")
    plan, new, _ = fork_calls(tmp_path, source)
    why = reasons(plan)
    assert why[("timeline", "01")] == "input_changed:lyrics"
    assert why[("render", "01")] == "downstream_of:timeline"
    assert why[("timeline", "02")] == "unchanged"
    assert why[("render", "02")] == "unchanged"
    assert new.output("render", "01") == "one, rewritten (noir)"


def test_ac10_fork_diff_missing_input_file_counts_as_unchanged(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    (tmp_path / "songs" / "02.md").unlink()
    plan, new, _ = fork_calls(tmp_path, source)
    assert reasons(plan)[("timeline", "02")] == "unchanged"
    assert new.output("render", "02") == "two (noir)"  # the source's snapshot came along


def test_ac10_fork_diff_source_edit(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    plan, new, _ = fork_calls(tmp_path, source, shot_suffix="!")
    assert reasons(plan)[("shotlist", "01")] == "source_changed"
    assert new.output("render", "01") == "one! (noir)"


def test_ac10_fork_diff_workflow_version(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    plan, _, _ = fork_calls(tmp_path, source, wf_v="2")
    why = reasons(plan)
    assert why[("timeline", "01")] == "workflow_version_changed 1->2"
    assert why[("render", "02")] == "workflow_version_changed 1->2; downstream_of:styled"
    assert all(r.action == "run" for r in plan.rows)


def test_ac10_fork_diff_failed_source_step(tmp_path: Path) -> None:
    source = source_run(tmp_path, fail_render_02=True)
    assert source.status == "failed"
    plan, new, _ = fork_calls(tmp_path, source)
    why = reasons(plan)
    assert why[("render", "02")] == "not_done_in_source"
    assert why[("render", "01")] == "unchanged"
    assert new.status == "completed"


def test_ac10_fork_diff_missing_file_rerun_uses_the_source_snapshot(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    (tmp_path / "songs" / "01.md").unlink()
    wf = make(tmp_path / "flows", [])
    new = wf.open_run(source.run_id).fork(refresh=("timeline",))
    assert new.status == "completed"
    assert new.output("timeline", "01") == ["one"]  # read from the source run's copy of the lyrics


def test_ac10_fork_diff_new_step(tmp_path: Path) -> None:
    source = source_run(tmp_path)
    calls: list[tuple[str, str]] = []
    wf = make(tmp_path / "flows", calls)

    @wf.step()
    def caption(render: str) -> str:
        return render.upper()

    why = reasons(wf.open_run(source.run_id).fork(dry_run=True))
    assert why[("caption", "01")] == "new_step"
    assert why[("render", "01")] == "unchanged"
