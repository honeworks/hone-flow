"""AC-11: fork(dry_run=True) returns the plan, writes nothing, and matches what the fork records."""

from pathlib import Path

import pytest

import hone_flow as fk
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def listing(folder: Path) -> list[tuple[str, int]]:
    return sorted((p.relative_to(folder).as_posix(), p.stat().st_mtime_ns) for p in folder.rglob("*"))


def test_ac11_dry_run_fork_plan(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    source = wf.run(write_songs(tmp_path), params={"style": "noir"})
    before = listing(tmp_path / "flows")
    plan = source.fork(refresh=("timeline",), dry_run=True)
    assert listing(tmp_path / "flows") == before  # nothing written
    assert isinstance(plan, fk.ForkPlan)
    assert plan.source_run_id == source.run_id
    why = {(r.step, r.item): (r.action, r.reason) for r in plan.rows}
    assert why[("style_guide", None)] == ("reuse", "unchanged")
    assert why[("timeline", "01")] == ("run", "refresh_requested")
    assert why[("shotlist", "01")] == ("run", "downstream_of:timeline")
    assert why[("review_shotlist", "02")] == ("run", "not_done_in_source; downstream_of:timeline")
    assert why[("render", "01")] == (
        "run",
        "not_done_in_source; downstream_of:review_shotlist",
    )  # the nearest cause
    assert [(r.step, r.item) for r in plan.rows][:3] == [
        ("style_guide", None),
        ("timeline", "01"),
        ("timeline", "02"),
    ]

    new = source.fork(refresh=("timeline",))
    recorded = new.manifest["fork_of"]["plan"]
    assert recorded == [
        {"item": r.item, "step": r.step, "action": r.action, "reason": r.reason} for r in plan.rows
    ]


def test_ac11_dry_run_fork_plan_errors(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    source = wf.run(write_songs(tmp_path), params={"style": "noir"})
    with pytest.raises(fk.WorkflowDefinitionError, match=r"refresh names unknown steps \['shotlst'\]"):
        source.fork(refresh="shotlst", dry_run=True)
    with pytest.raises(fk.WorkflowDefinitionError, match="has no item '09'"):
        source.fork(items=["09"], dry_run=True)
    detached = fk.Run(wf.storage, f"song_video/runs/{source.run_id}")
    with pytest.raises(fk.HoneFlowError, match="fork needs the workflow's code"):
        detached.fork(dry_run=True)
