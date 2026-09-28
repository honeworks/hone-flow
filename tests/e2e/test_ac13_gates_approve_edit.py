"""AC-13: approve and edit gates."""

import getpass
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import hone_flow as fk
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac13_gates_approve_edit(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = song_video(tmp_path / "flows", calls)
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    assert run.status == "awaiting_review"
    assert run.steps("render", "01")[0].status == "pending"  # downstream of a waiting gate stays pending

    run.approve(step="review_shotlist", item="01", note="looks right", actor="ana")
    edited = {"shots": [{"line": "edited line", "look": "noir"}], "seed": 0, "note": None}
    run.edit(step="review_shotlist", item="02", value=edited)
    calls.clear()
    run.resume()
    assert run.status == "completed"
    assert calls == [("render", "01"), ("render", "02")]  # nothing upstream reran
    assert run.output("render", "02").path.read_text() == "edited line"  # downstream got the edited value

    gate_01 = run.steps("review_shotlist", "01")[0]
    assert gate_01.labels == ["approved"]
    assert [(r["decision"], r["actor"], r["note"]) for r in gate_01.reviews] == [
        ("approved", "ana", "looks right")
    ]
    gate_02 = run.steps("review_shotlist", "02")[0]
    assert gate_02.labels == ["edited"]
    assert gate_02.attempt == 2
    assert [(a["attempt"], a["status"]) for a in gate_02.attempts] == [(1, "replaced")]
    assert gate_02.reviews[0]["decision"] == "edited"
    assert gate_02.reviews[0]["actor"] == getpass.getuser()  # $USER when no actor is given
    folder = Path(run.location)
    old = json.loads((folder / "review_shotlist/item_02/attempts/1/output/review_shotlist.json").read_text())
    assert old["shots"][0]["line"] == "only line"  # the old gate output, kept
    assert json.loads((folder / "review_shotlist/item_02/output/review_shotlist.json").read_text()) == edited

    decisions = [
        s
        for s in run.spans()
        if s["name"] == "hone.flow.gate" and "hone.flow.gate.decision" in s["attributes"]
    ]
    assert [
        (
            s["attributes"]["hone.flow.gate.decision"],
            s["attributes"]["hone.flow.gate.actor"],
            s["attributes"]["hone.item"],
        )
        for s in decisions
    ] == [("approved", "ana", "01"), ("edited", getpass.getuser(), "02")]
    assert all(s["trace_id"] == run.manifest["trace_id"] for s in decisions)  # in the run's trace


def test_ac13_gates_approve_edit_errors(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    with pytest.raises(fk.ReviewError, match="'shotlist' is not a gate"):
        run.approve(step="shotlist", item="01")
    with pytest.raises(fk.ReviewError, match="pass item="):
        run.approve(step="review_shotlist")
    run.approve(step="review_shotlist", item="01")
    with pytest.raises(fk.ReviewError, match="not awaiting review for item '01' \\(it is done\\)"):
        run.approve(step="review_shotlist", item="01")
    with pytest.raises(fk.ReviewError, match="not awaiting review"):
        run.reject(step="review_shotlist", item="01", note="too late")


def test_ac13_gates_approve_edit_takes_the_run_lease(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    now = datetime.now(UTC)
    lease = {
        "owner": "other",
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
    }
    (Path(run.location) / "lease.json").write_text(json.dumps(lease))
    with pytest.raises(fk.RunLocked):
        run.approve(step="review_shotlist", item="01")
    assert run.steps("review_shotlist", "01")[0].status == "awaiting_review"


def test_ac13_gates_approve_edit_validates_pydantic_values(tmp_path: Path) -> None:
    from tests.e2e.song_video import Timeline

    wf = fk.Workflow("typed_gate", storage=tmp_path)

    @wf.step()
    def draft(text: str) -> Timeline:
        return Timeline(duration=1.0, lines=[text])

    @wf.gate()
    def check(draft: Timeline) -> Timeline:
        return draft

    @wf.step()
    def use(check: Timeline) -> float:
        return check.duration

    run = wf.run([fk.Item("01", {"text": "a"})])
    with pytest.raises(fk.ReviewError, match="not a valid"):
        run.edit(step="check", item="01", value={"duration": "long"})
    run.edit(step="check", item="01", value={"duration": 9.5, "lines": ["b"]})
    run.resume()
    assert run.output("use", "01") == 9.5
    assert run.output("check", "01") == Timeline(duration=9.5, lines=["b"])


def test_ac13_gates_approve_edit_error_paths_for_edit(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    with pytest.raises(fk.ReviewError, match="not a gate"):
        run.edit(step="timeline", item="01", value={})
    with pytest.raises(fk.ReviewError, match="pass item="):
        run.edit(step="review_shotlist", value={})
    run.approve(step="review_shotlist", item="01")
    assert run.steps("review_shotlist", "01")[0].labels == ["approved"]  # before any resume
    with pytest.raises(fk.ReviewError, match="not awaiting review"):
        run.edit(step="review_shotlist", item="01", value={})


def test_ac13_gates_approve_edit_takes_over_a_stale_lease(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    folder = Path(run.location)
    past = (datetime.now(UTC) - timedelta(seconds=90)).isoformat()
    lease = {
        "owner": "gone",
        "host": "other-box",
        "pid": 1,
        "acquired_at": past,
        "heartbeat_at": past,
        "expires_at": past,
    }
    (folder / "lease.json").write_text(json.dumps(lease))
    (folder / "shotlist/item_02/attempts/5/output").mkdir(parents=True)  # debris the crashed call left
    (folder / "shotlist/item_02/attempts/5/output/shotlist.json").write_text("{}")
    run.approve(step="review_shotlist", item="01")
    assert not (folder / "shotlist/item_02/attempts/5").exists()  # the takeover repaired the folder
    assert run.steps("review_shotlist", "01")[0].status == "done"
    assert not (folder / "lease.json").exists()
