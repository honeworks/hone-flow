"""AC-1: the design §3 example (design/current.md) end to end."""

from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.notifications import HttpWebhook
from hone_flow.testing import WebhookServer
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac1_song_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    server = WebhookServer()
    with server:
        monkeypatch.setenv("OPS_WEBHOOK_URL", server.url)
        calls: list[tuple[str, str]] = []
        ops = HttpWebhook(
            name="ops", url_env="OPS_WEBHOOK_URL", events=("run.completed", "run.awaiting_review")
        )
        wf = song_video(tmp_path / "flows", calls, notifications=[ops])
        flow(wf, tmp_path, calls)
    events = [r.json()["event"] for r in server.requests]
    assert events == ["run.awaiting_review", "run.awaiting_review", "run.completed", "run.awaiting_review"]


def flow(wf: fk.Workflow, tmp_path: Path, calls: list[tuple[str, str]]) -> None:
    run = wf.run(write_songs(tmp_path), params={"style": "silhouette"})
    assert run.status == "awaiting_review"  # stopped at the gate; the process may exit

    run = wf.open_run(run.run_id)  # later, any process that imports the same workflow
    run.approve(step="review_shotlist", item="01")
    run.reject(step="review_shotlist", item="02", note="darker lighting")
    calls.clear()
    run.resume()  # 01 renders; 02's shotlist reruns with review_note, gate pauses again
    assert calls == [("shotlist", "02"), ("render", "01")]
    assert run.output("shotlist", "02")["note"] == "darker lighting"
    assert run.status == "awaiting_review"
    run.approve(step="review_shotlist", item="02")
    run.resume()
    assert run.status == "completed"
    assert run.output("render", "01").path.name == "video.txt"
    assert run.output("render", "02").path.read_text() == "only line"
    assert run.steps("review_shotlist", "01")[0].labels == ["approved"]
    assert run.steps("shotlist", "02")[0].attempt == 2

    plan = run.fork(dry_run=True)  # nothing changed: every row is "reuse"
    assert {row.action for row in plan.rows} == {"reuse"}
    new = run.fork(refresh=("shotlist",))  # new run id beside the source; shotlist + downstream rerun
    assert new.manifest["fork_of"]["run_id"] == run.run_id
    assert new.run_id != run.run_id
    assert new.status == "awaiting_review"  # the gates downstream of shotlist pause again
    run.pin()
    report = wf.cleanup(keep_last=1)
    assert report.deleted == (run.run_id,)  # the pinned run's original; its pinned copy stays
    assert [s.run_id for s in wf.runs()] == [new.run_id, run.run_id]
    assert wf.open_run(run.run_id).pinned
