"""AC-35: the read API for browsers and dashboards (design change 0008): run ids without reading
manifests, summaries of one page, one-level listings, sizes and byte ranges, lease state, attempt counts."""

import os
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk
from hone_flow.testing import MemoryStorage

pytestmark = pytest.mark.e2e


def flow(storage: Any, fail: set[str]) -> fk.Workflow:
    wf = fk.Workflow("browse", storage=storage)

    @wf.step()
    def render(text: str, ctx: fk.Context) -> fk.File:
        if text in fail:
            raise ValueError("no")
        out = ctx.new_file("clip.bin")
        out.write_bytes(bytes(range(256)) * 4)
        return fk.File(out)

    return wf


class CountingStorage(MemoryStorage):
    """Counts manifest reads, to show which calls read manifests."""

    def __init__(self) -> None:
        super().__init__()
        self.manifest_reads = 0

    def read_bytes(self, key: str) -> bytes:
        if key.endswith("/manifest.json"):
            self.manifest_reads += 1
        return super().read_bytes(key)


@pytest.mark.parametrize("kind", ["local", "memory", "fsspec"])
def test_ac35_run_ids_and_summaries(tmp_path: Path, kind: str) -> None:
    storage: Any = {
        "local": tmp_path / "flows",
        "memory": MemoryStorage(),
        "fsspec": f"memory://ac35-{os.getpid()}-{kind}/proj",
    }[kind]
    wf = flow(storage, fail={"b"})
    first = wf.run([fk.Item("a", {"text": "a"}), fk.Item("b", {"text": "b"})])
    flow(storage, fail=set()).open_run(first.run_id).resume()  # b: attempt 2
    second = wf.run([fk.Item("a", {"text": "a"})], label="second")
    second.pin()

    history = fk.open_runs(wf.storage, "browse")
    ids = history.run_ids()
    assert set(ids) == {first.run_id, second.run_id}
    assert ids == sorted(ids, reverse=True)
    (summary,) = history.summaries([first.run_id])
    assert summary.attempts == {"render/b": 2}
    assert summary.status == "completed"
    page = history.summaries([second.run_id, "20990101T000000Z-000000", first.run_id])
    assert [s.run_id for s in page] == [second.run_id, first.run_id]  # unknown ids are left out
    assert page[0].pinned and not page[1].pinned
    assert page[0].label == "second"
    assert {s.run_id for s in history.runs()} == set(ids)
    assert first.manifest["attempts"] == {"render/b": 2}
    assert [(r.step, r.item, r.attempt) for r in first.steps()] == [("render", "a", 1), ("render", "b", 2)]

    # one-level listing, sizes and byte ranges of any file in a run folder, through the storage
    from hone_flow.storage import file_size, list_dir, read_range

    store = wf.storage
    root = [e.name for e in list_dir(store, f"browse/runs/{first.run_id}")]
    assert {"manifest.json", "render", "spans.jsonl", "reports"} <= set(root)
    (clip,) = list_dir(store, f"browse/runs/{first.run_id}/render/item_a/output")
    assert (clip.name, clip.is_dir, clip.size) == ("clip.bin", False, 1024)
    key = f"browse/runs/{first.run_id}/render/item_a/output/clip.bin"
    assert file_size(store, key) == 1024
    assert read_range(store, key, 255, 3) == bytes([255, 0, 1])


def test_ac35_run_ids_read_no_manifest(tmp_path: Path) -> None:
    storage = CountingStorage()
    wf = flow(storage, fail=set())
    runs = [wf.run([fk.Item("a", {"text": "a"})]).run_id for _ in range(3)]
    history = fk.open_runs(storage, "browse")
    storage.manifest_reads = 0
    assert set(history.run_ids()) == set(runs)
    assert storage.manifest_reads == 0
    history.summaries(history.run_ids()[:1])
    assert storage.manifest_reads == 1  # one page reads only its own manifests


def test_ac35_lease_state(tmp_path: Path) -> None:
    wf = flow(tmp_path / "flows", fail=set())
    seen: list[fk.LeaseInfo | None] = []

    @wf.step()
    def watch(render: fk.File, ctx: fk.Context) -> int:
        seen.append(fk.open_runs(tmp_path / "flows", "browse").open_run(ctx.run_id).lease())
        return 1

    run = wf.run([fk.Item("a", {"text": "a"})])
    (held,) = seen
    assert held is not None
    assert (held.live, held.pid, held.host) == (True, os.getpid(), socket.gethostname())
    assert run.lease() is None  # nobody holds a finished run

    stale = {
        "owner": "x",
        "host": "elsewhere",
        "pid": 1,
        "acquired_at": "2026-01-01T00:00:00.000Z",
        "heartbeat_at": "2026-01-01T00:00:00.000Z",
        "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
    }
    import json

    Path(run.location, "lease.json").write_text(json.dumps(stale))
    lease = run.lease()
    assert lease is not None
    assert (lease.live, lease.host) == (False, "elsewhere")  # a dead holder: the run is not really running
