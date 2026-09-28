"""AC-8: partial runs with until / items_filter, finished by resume."""

from pathlib import Path

import pytest

import hone_flow as fk
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac8_partial_runs(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = song_video(tmp_path / "flows", calls)
    items = write_songs(tmp_path)
    run = wf.run(items, params={"style": "noir"}, until="shotlist")
    assert run.status == "partial"
    assert {c[0] for c in calls} == {"style_guide", "timeline", "shotlist"}  # shotlist and its upstream
    assert {r.step: r.status for r in run.steps(item="01")} == {
        "timeline": "done",
        "shotlist": "done",
        "review_shotlist": "skipped",
        "render": "skipped",
    }

    calls.clear()
    other = wf.run(items, params={"style": "noir"}, items_filter=["01"])
    assert other.status == "awaiting_review"  # awaiting_review wins over partial
    assert {c[1] for c in calls} == {"_global", "01"}
    assert [r.status for r in other.steps(item="02")] == ["skipped"] * 4

    calls.clear()
    run.resume(items=["02"])
    assert {
        c[1] for c in calls if c[0] != "style_guide"
    } == set()  # 02's steps upstream of the gate were done
    assert run.steps("review_shotlist", "02")[0].status == "awaiting_review"
    assert run.steps("review_shotlist", "01")[0].status == "skipped"  # outside the call
    run.resume()
    assert run.steps("review_shotlist", "01")[0].status == "awaiting_review"
    assert run.status == "awaiting_review"
    for item in ("01", "02"):
        run.approve(step="review_shotlist", item=item)
    run.resume()
    assert run.status == "completed"  # the partial run finished through resume


def test_ac8_partial_runs_resume_until(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = song_video(tmp_path / "flows", calls)
    run = wf.run(write_songs(tmp_path), params={"style": "noir"}, until="timeline")
    assert {c[0] for c in calls} == {"timeline"}  # the global step is not upstream of timeline
    calls.clear()
    run.resume(until="shotlist", items=["01"])
    assert calls == [("style_guide", "_global"), ("shotlist", "01")]
    assert run.status == "partial"
    with pytest.raises(fk.WorkflowDefinitionError, match="until='nope' is not a step"):
        run.resume(until="nope")
    with pytest.raises(fk.WorkflowDefinitionError, match="until='nope' is not a step"):
        wf.run(write_songs(tmp_path), params={"style": "x"}, until="nope")
