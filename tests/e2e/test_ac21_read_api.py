"""AC-21: reading runs without the workflow's code."""

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

import hone_flow as fk
from tests.e2e.song_video import Timeline, song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac21_read_api(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")

    @wf.step()
    def fragile(timeline: Timeline, ctx: fk.Context) -> int:
        if ctx.item_id == "02":
            raise ValueError("bad timeline")
        return len(timeline.lines)

    @wf.step()
    def after(fragile: int) -> int:
        return fragile

    first = wf.run(write_songs(tmp_path), params={"style": "noir"}, until="fragile")
    time.sleep(0.01)
    marker = first.manifest["updated_at"]
    time.sleep(0.01)
    second = wf.run(write_songs(tmp_path), params={"style": "noir"})
    second.reject(step="review_shotlist", item="01", note="warmer")

    history = fk.open_runs(tmp_path / "flows", "song_video")  # no workflow code involved
    summaries = history.runs()
    assert [s.run_id for s in summaries] == [second.run_id, first.run_id]  # newest first
    assert [s.run_id for s in history.runs(updated_since=marker)] == [second.run_id, first.run_id]
    assert [s.run_id for s in history.runs(updated_since=second.manifest["created_at"])] == [second.run_id]
    summary = summaries[0]
    assert (summary.workflow, summary.workflow_version, summary.status) == ("song_video", "1", "failed")
    assert summary.items == ("01", "02")
    assert summary.fork_of is None
    assert not summary.pinned

    run = history.open_run(second.run_id)
    records = {(r.step, r.item): r for r in run.steps()}
    assert records[("fragile", "02")].status == "failed"
    assert records[("fragile", "02")].error["message"] == "ValueError: bad timeline"  # type: ignore[index]
    assert records[("after", "02")].status == "blocked"
    assert records[("shotlist", "01")].status == "pending"  # rejected: rerun on resume
    assert records[("shotlist", "01")].attempts[0]["status"] == "rejected"
    assert records[("review_shotlist", "01")].reviews[0]["note"] == "warmer"
    assert records[("review_shotlist", "02")].status == "awaiting_review"
    assert records[("render", "02")].status == "pending"
    assert records[("style_guide", None)].location.endswith(f"/runs/{second.run_id}/style_guide/")
    skipped = {(r.step, r.item): r.status for r in history.open_run(first.run_id).steps()}
    assert skipped[("render", "01")] == "skipped"
    assert run.output("timeline", "01") == Timeline(duration=8.0, lines=["first line", "second line"])
    with pytest.raises(fk.HoneFlowError, match="resume needs the workflow's code"):
        run.resume()
    with pytest.raises(fk.HoneFlowError, match="fork needs the workflow's code"):
        run.fork()
    run.approve(step="review_shotlist", item="02", actor="lens")  # detached runs can review
    assert run.steps("review_shotlist", "02")[0].labels == ["approved"]
    assert run.manifest["notifications"] == []  # no destinations configured (AC-17 shows the states)

    rendered = fk.open_runs(tmp_path / "flows", "song_video").open_run(second.run_id)
    assert history.runs(updated_since=datetime.fromisoformat(marker)) == history.runs(updated_since=marker)
    naive = datetime.fromisoformat(marker.replace("Z", "")).replace(tzinfo=None)
    assert [s.run_id for s in history.runs(updated_since=naive)] == [
        second.run_id,
        first.run_id,
    ]  # naive = UTC
    with pytest.raises(fk.HoneFlowError, match="not an ISO-8601 time"):
        history.runs(updated_since="last tuesday")
    assert rendered.steps("render", "02")[0].status == "pending"

    reused = wf.open_run(first.run_id).fork(refresh=("fragile",))
    forked = fk.open_runs(tmp_path / "flows", "song_video").open_run(reused.run_id)
    assert forked.steps("timeline", "01")[0].reused_from == first.run_id
    assert fk.open_runs(tmp_path / "flows", "song_video").runs()[0].fork_of == first.run_id
    with pytest.raises(fk.RunNotFound):
        history.open_run("20000101T000000Z-000000")


def test_ac21_read_api_unknown_format_version(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    manifest_path = Path(run.location) / "manifest.json"
    data = json.loads(manifest_path.read_text()) | {"format_version": "2"}
    manifest_path.chmod(0o644)
    manifest_path.write_text(json.dumps(data))
    with pytest.raises(fk.HoneFlowError, match="upgrade hone-flow"):
        fk.open_runs(tmp_path / "flows", "song_video").runs()
    with pytest.raises(fk.HoneFlowError, match="upgrade hone-flow"):
        _ = fk.open_runs(tmp_path / "flows", "song_video").open_run(run.run_id).status


def test_ac21_read_api_detached_file_outputs(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    run.approve(step="review_shotlist", item="01")
    run.resume()
    detached = fk.open_runs(tmp_path / "flows", "song_video").open_run(run.run_id)
    video = detached.output("render", "01")
    assert isinstance(video, fk.File)
    assert video.path.read_text() == "first line\nsecond line"
    detached.edit(step="review_shotlist", item="02", value={"shots": [{"line": "x"}]}, actor="lens")
    (gate,) = detached.steps("review_shotlist", "02")
    assert (gate.labels, gate.reviews[0]["actor"]) == (["edited"], "lens")


def test_ac21_read_api_notification_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hone_flow.notifications import HttpWebhook
    from hone_flow.testing import WebhookServer

    with WebhookServer() as server:
        monkeypatch.setenv("READ_API_HOOK", server.url)
        wf = song_video(
            tmp_path / "flows",
            notifications=[HttpWebhook(name="ops", url_env="READ_API_HOOK", events=("run.awaiting_review",))],
        )
        run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    detached = fk.open_runs(tmp_path / "flows", "song_video").open_run(run.run_id)
    (event,) = detached.manifest["notifications"]
    assert event["event"] == "run.awaiting_review"
    assert event["deliveries"]["ops"]["state"] == "delivered"
