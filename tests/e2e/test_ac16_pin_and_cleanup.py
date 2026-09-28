"""AC-16: pinning copies a finished run; cleanup never touches pinned copies or runs in use."""

import hashlib
import json
import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import hone_flow as fk
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def finished(wf: fk.Workflow, tmp_path: Path) -> fk.Run:
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    for item in ("01", "02"):
        run.approve(step="review_shotlist", item=item)
    run.resume()
    return run


def hashes(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in folder.rglob("*")
        if p.is_file()
    }


def test_ac16_pin_and_cleanup(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    waiting = wf.run(write_songs(tmp_path), params={"style": "noir"})
    with pytest.raises(
        fk.HoneFlowError, match="awaiting_review; only completed or failed runs can be pinned"
    ):
        waiting.pin()
    run = finished(wf, tmp_path)
    run.pin()
    runs_folder = tmp_path / "flows" / "song_video" / "runs" / run.run_id
    pinned_folder = tmp_path / "flows" / "song_video" / "pinned_runs" / run.run_id
    original, copy = hashes(runs_folder), hashes(pinned_folder)
    assert set(copy) == set(original)
    assert {k: v for k, v in copy.items() if k != "manifest.json"} == {
        k: v for k, v in original.items() if k != "manifest.json"
    }
    for folder in (runs_folder, pinned_folder):
        manifest = json.loads((folder / "manifest.json").read_text())
        assert manifest["pinned"] is True
        assert manifest["pinned_at"]
    assert json.loads((pinned_folder / "manifest.json").read_text())["location"] == f"{pinned_folder}/"
    assert run.pinned
    listed = [s for s in wf.runs() if s.run_id == run.run_id]
    assert len(listed) == 1  # one logical run
    assert listed[0].pinned
    assert listed[0].location == f"{runs_folder}/"

    newer = finished(wf, tmp_path)
    with pytest.raises(fk.HoneFlowError, match="keep_last=, older_than= or both"):
        wf.cleanup()
    report = wf.cleanup(keep_last=1, dry_run=True)
    assert set(report.deleted) == {run.run_id, waiting.run_id}
    assert report.kept == (newer.run_id,)
    assert runs_folder.exists()  # a dry run deletes nothing
    assert wf.cleanup(keep_last=1) == report
    assert not runs_folder.exists()  # the pinned run's original is cleaned up too ...
    assert pinned_folder.exists()  # ... never its pinned copy
    assert [s.run_id for s in wf.runs()] == [newer.run_id, run.run_id]

    archived = wf.open_run(run.run_id)  # reads the archive
    assert archived.location == f"{pinned_folder}/"
    assert archived.status == "completed"
    assert archived.output("render", "01").path.read_text() == "first line\nsecond line"
    with pytest.raises(fk.HoneFlowError, match="pinned archive; resume is not allowed: fork it instead"):
        archived.resume()
    with pytest.raises(fk.HoneFlowError, match="pinned archive"):
        archived.approve(step="review_shotlist", item="01")
    fork = archived.fork(refresh=("render",))
    assert fork.manifest["fork_of"]["run_id"] == run.run_id
    assert fork.steps("timeline", "01")[0].labels == ["reused"]
    assert hashes(pinned_folder) == copy  # the archive never changes


def test_ac16_pin_and_cleanup_skips_live_leases_and_honours_age(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    old, busy, new = (finished(wf, tmp_path) for _ in range(3))
    now = datetime.now(UTC)
    lease = {
        "owner": "x",
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
    }
    (Path(busy.location) / "lease.json").write_text(json.dumps(lease))
    assert wf.cleanup(older_than=timedelta(days=1)).deleted == ()  # all younger than a day
    report = wf.cleanup(keep_last=1, older_than=timedelta(0))
    assert report.locked == (busy.run_id,)
    assert report.deleted == (old.run_id,)
    assert report.kept == (new.run_id,)
    assert Path(busy.location).exists()


class CorruptingCopies(fk.LocalStorage):
    """Copies one file wrongly, like a flaky backend."""

    def copy(self, src_key: str, dst_key: str) -> None:
        if dst_key.endswith("timeline.json") and "pinned_runs" in dst_key:
            self.write_bytes(dst_key, b"garbage")
            return
        super().copy(src_key, dst_key)


def test_ac16_pin_and_cleanup_pin_rules(tmp_path: Path) -> None:
    wf = fk.Workflow("pins", storage=CorruptingCopies(tmp_path))

    @wf.step()
    def timeline(text: str) -> str:
        if text == "boom":
            raise ValueError("boom")
        return text

    failed = wf.run([fk.Item("01", {"text": "boom"})])
    assert failed.status == "failed"
    failed.pin()  # a failed run is finished too
    with pytest.raises(fk.HoneFlowError, match="already pinned"):
        failed.pin()
    ok = wf.run([fk.Item("01", {"text": "fine"})])
    with pytest.raises(fk.HoneFlowError, match="does not match the original; nothing was pinned"):
        ok.pin()
    assert not (tmp_path / "pins" / "pinned_runs" / ok.run_id).exists()
    assert {s.run_id: s.pinned for s in wf.runs()} == {failed.run_id: True, ok.run_id: False}
    assert not ok.pinned
    now = datetime.now(UTC)
    lease = {
        "owner": "x",
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
    }
    (Path(ok.location) / "lease.json").write_text(json.dumps(lease))
    with pytest.raises(fk.RunLocked):
        ok.pin()


def test_ac16_pin_and_cleanup_options(tmp_path: Path) -> None:
    wf = song_video(tmp_path / "flows")
    first, second = finished(wf, tmp_path), finished(wf, tmp_path)
    with pytest.raises(fk.HoneFlowError, match="keep_last must be 0 or more"):
        wf.cleanup(keep_last=-1)
    assert wf.cleanup(older_than=timedelta(hours=1)).deleted == ()
    assert set(wf.cleanup(older_than=timedelta(0), dry_run=True).deleted) == {first.run_id, second.run_id}
    assert set(wf.cleanup(keep_last=0).deleted) == {first.run_id, second.run_id}
    assert wf.runs() == []
