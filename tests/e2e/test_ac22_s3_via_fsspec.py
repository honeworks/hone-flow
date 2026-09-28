"""AC-22: the §3 flow on fsspec storage (memory://); S3 itself is in tests/integration/test_s3_storage.py."""

import uuid
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.testing import MemoryStorage
from hone_flow.testing.contracts import check_run_storage
from tests.e2e.song_video import Timeline, song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac22_s3_via_fsspec(tmp_path: Path) -> None:
    url = f"memory://bucket-{uuid.uuid4().hex[:8]}/proj"
    wf = song_video(url)
    assert isinstance(wf.storage, fk.FsspecStorage)
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    assert run.location == f"{url}/song_video/runs/{run.run_id}/"
    run.approve(step="review_shotlist", item="01")
    run.reject(step="review_shotlist", item="02", note="darker")
    run.resume()
    run.approve(step="review_shotlist", item="02")
    run.resume()
    assert run.status == "completed"
    assert run.output("render", "02", local_dir=tmp_path / "out").path.read_text() == "only line"
    new = run.fork(refresh=("render",))
    assert new.steps("shotlist", "02")[0].reused_from == run.run_id
    run.pin()
    assert wf.cleanup(keep_last=1).deleted == (run.run_id,)
    history = fk.open_runs(url, "song_video")
    assert [(s.run_id, s.pinned) for s in history.runs()] == [(new.run_id, False), (run.run_id, True)]
    archived = history.open_run(run.run_id)
    assert archived.output("timeline", "01") == Timeline(duration=8.0, lines=["first line", "second line"])
    assert archived.steps("shotlist", "02")[0].attempts[0]["note"] == "darker"
    assert any(s["name"] == "hone.flow.gate" for s in archived.spans())


def test_ac22_s3_via_fsspec_storage_contracts(tmp_path: Path) -> None:
    check_run_storage(fk.LocalStorage(tmp_path / "local"), tmp_path)
    check_run_storage(MemoryStorage(), tmp_path)
    check_run_storage(fk.FsspecStorage(f"memory://contract-{uuid.uuid4().hex[:8]}/x"), tmp_path)
