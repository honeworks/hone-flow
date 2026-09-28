"""AC-12: a fork is how one step is rerun for some items with new params (hone-lens' StepRerunner)."""

from pathlib import Path

import pytest

import hone_flow as fk
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac12_fork_as_step_rerun(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = song_video(tmp_path / "flows", calls)
    source = wf.run(write_songs(tmp_path), params={"style": "silhouette"})
    calls.clear()
    new = wf.open_run(source.run_id).fork(refresh=("shotlist",), items=["02"], params={"style": "noir"})
    assert [i["id"] for i in new.manifest["items"]] == ["02"]  # only the chosen items
    assert calls == [("style_guide", "_global"), ("shotlist", "02")]  # the global step reruns: style changed
    assert new.output("shotlist", "02")["shots"][0]["look"] == "noir"
    why = {(r["step"], r["item"]): r["reason"] for r in new.manifest["fork_of"]["plan"]}
    assert why[("style_guide", None)] == "param_changed:style"
    assert why[("shotlist", "02")] == "refresh_requested; downstream_of:style_guide"
    assert why[("timeline", "02")] == "unchanged"
    assert new.manifest["params"] == {"style": "noir"}
    assert new.status == "awaiting_review"
    assert new.steps("timeline", "02")[0].reused_from == source.run_id
    assert new.steps(item="01") == []
    assert not (Path(new.location) / "timeline" / "item_01").exists()


def test_ac12_fork_as_step_rerun_new_and_replaced_items(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    items = write_songs(tmp_path)
    source = wf.run(items, params={"style": "noir"})
    (tmp_path / "songs" / "03.md").write_text("third song\n")
    (tmp_path / "other.md").write_text("replacement lyrics\n")
    replaced = fk.Item("01", {"lyrics": fk.File(tmp_path / "other.md")})
    added = fk.Item("03", {"lyrics": fk.File(tmp_path / "songs" / "03.md")})
    plan = source.fork(items=[replaced, "02", added], dry_run=True)
    why = {(r.step, r.item): r.reason for r in plan.rows}
    assert why[("timeline", "01")] == "input_changed:lyrics"
    assert why[("timeline", "02")] == "unchanged"
    assert why[("timeline", "03")] == "new_item"
    assert why[("shotlist", "03")] == "new_item; downstream_of:timeline"
    new = source.fork(items=[replaced, "02", added])
    assert new.output("timeline", "01").lines == ["replacement lyrics"]
    assert new.output("timeline", "03").lines == ["third song"]
