"""FsspecStorage against S3 (a local moto server; no network): run, fork, pin, read back (AC-22)."""

import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk
from hone_flow.testing.contracts import check_run_storage
from tests.e2e.song_video import song_video, write_songs

pytest.importorskip("s3fs", reason="S3 storage needs the 's3' extra")
moto_server = pytest.importorskip("moto.server", reason="needs moto[server] (dev extra)")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def s3_options() -> Iterator[dict[str, Any]]:
    port = free_port()
    server = moto_server.ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    server.start()
    try:
        yield {
            "key": "test",
            "secret": "test",
            "client_kwargs": {"endpoint_url": f"http://127.0.0.1:{port}", "region_name": "eu-west-1"},
        }
    finally:
        server.stop()


def test_s3_storage_contract(s3_options: dict[str, Any], tmp_path: Path) -> None:
    storage = fk.FsspecStorage("s3://hone-flow-contract/root", **s3_options)
    storage.fs.mkdir("hone-flow-contract")
    check_run_storage(storage, tmp_path)


def test_s3_storage_run_fork_pin(s3_options: dict[str, Any], tmp_path: Path) -> None:
    storage = fk.FsspecStorage("s3://hone-flow-test/projects/oneshotstudio", **s3_options)
    storage.fs.mkdir("hone-flow-test")
    wf = song_video(storage)
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    assert run.location == f"s3://hone-flow-test/projects/oneshotstudio/song_video/runs/{run.run_id}/"
    assert storage.exists(f"song_video/runs/{run.run_id}/timeline/item_01/output/timeline.json")
    for item in ("01", "02"):
        run.approve(step="review_shotlist", item=item)
    run.resume()
    assert run.status == "completed"
    video = run.output("render", "01", local_dir=tmp_path / "download")
    assert video.path == tmp_path / "download" / "video.txt"
    assert video.path.read_text() == "first line\nsecond line"

    fork = run.fork(refresh=("render",))
    assert fork.status == "completed"
    assert fork.steps("timeline", "01")[0].reused_from == run.run_id  # a server-side copy
    run.pin()
    assert wf.cleanup(keep_last=1).deleted == (run.run_id,)
    history = fk.open_runs(storage, "song_video")
    assert {(s.run_id, s.pinned) for s in history.runs()} == {(run.run_id, True), (fork.run_id, False)}
    archived = history.open_run(run.run_id)
    assert archived.location.endswith(f"/pinned_runs/{run.run_id}/")
    assert archived.output("timeline", "02").lines == ["only line"]
    assert len(archived.spans()) > 0
