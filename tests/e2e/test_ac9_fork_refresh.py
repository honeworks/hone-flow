"""AC-9: fork(refresh=...) reruns a step and its downstream, and copies the rest."""

import hashlib
import shutil
from pathlib import Path

import pytest

from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files_and_hashes(folder: Path) -> dict[str, str]:
    return {p.relative_to(folder).as_posix(): sha(p) for p in folder.rglob("*") if p.is_file()}


def test_ac9_fork_refresh(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = song_video(tmp_path / "flows", calls)
    source = wf.run(write_songs(tmp_path), params={"style": "noir"})
    for item in ("01", "02"):
        source.approve(step="review_shotlist", item=item)
    source.resume()
    assert source.status == "completed"
    source_folder = Path(source.location)
    before = files_and_hashes(source_folder)

    calls.clear()
    new = source.fork(refresh=("shotlist",))
    assert new.run_id != source.run_id
    new_folder = Path(new.location)
    assert new_folder.parent == source_folder.parent  # beside the source under runs/
    assert calls == [("shotlist", "01"), ("shotlist", "02")]  # shotlist reruns for all items ...
    assert new.status == "awaiting_review"  # ... and the gates downstream pause again
    assert new.manifest["fork_of"]["run_id"] == source.run_id
    assert new.output("shotlist", "01")["seed"] != source.output("shotlist", "01")["seed"]  # a new sample

    for step, item in [("timeline", "01"), ("timeline", "02"), ("style_guide", None)]:
        record = new.steps(step, item)[0] if item else new.steps(step)[0]
        assert record.status == "done"
        assert record.labels == ["reused"]
        assert record.reused_from == source.run_id
        unit = step if item is None else f"{step}/item_{item}"
        for part in ("output", "inputs"):
            for path in (source_folder / unit / part).rglob("*"):
                if path.is_file():
                    assert sha(
                        new_folder / unit / part / path.relative_to(source_folder / unit / part)
                    ) == sha(path)
    assert new.steps("render", "01")[0].status == "pending"
    reused_spans = [s for s in new.spans() if s["attributes"].get("hone.flow.reused_from") == source.run_id]
    assert len(reused_spans) == 3

    assert files_and_hashes(source_folder) == before  # the source is untouched
    shutil.rmtree(source_folder)  # copies, not references: the fork stands alone
    assert new.output("timeline", "01").lines == ["first line", "second line"]
    assert new.output("style_guide")["style"] == "noir"
    new.approve(step="review_shotlist", item="01")
    new.approve(step="review_shotlist", item="02")
    new.resume()
    assert new.status == "completed"
    assert new.output("render", "02").path.read_text() == "only line"


def test_ac9_fork_refresh_reused_gates_keep_their_decisions(tmp_path: Path) -> None:
    import json
    import os
    import socket
    from datetime import UTC, datetime, timedelta

    wf = song_video(tmp_path / "flows")
    source = wf.run(write_songs(tmp_path), params={"style": "noir"})
    source.reject(step="review_shotlist", item="02", note="again")
    source.resume()
    source.approve(step="review_shotlist", item="01")
    source.edit(step="review_shotlist", item="02", value={"shots": [{"line": "x", "look": "noir"}]})
    source.resume()
    now = datetime.now(UTC)
    lease = {
        "owner": "x",
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "acquired_at": now.isoformat(),
        "heartbeat_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=60)).isoformat(),
    }
    (Path(source.location) / "lease.json").write_text(json.dumps(lease))  # someone holds the source: fine
    new = source.fork()
    assert new.status == "completed"
    gate_01, gate_02 = new.steps("review_shotlist")
    assert gate_01.labels == ["reused", "approved"]
    assert gate_02.labels == ["reused", "edited"]
    assert [r["decision"] for r in gate_02.reviews] == ["rejected", "edited"]
    shotlist = new.steps("shotlist", "02")[0]
    assert (shotlist.attempt, shotlist.attempts, shotlist.labels) == (1, [], ["reused"])
    assert new.manifest["fork_of"]["plan"][0]["action"] == "reuse"
    assert (
        json.loads((Path(new.location) / "shotlist/item_02/metadata.json").read_text())["source_attempt"] == 2
    )
    assert not (Path(new.location) / "shotlist/item_02/attempts").exists()  # attempts are not copied
    (run_span,) = [s for s in new.spans() if s["name"] == "hone.flow.run"]
    assert run_span["attributes"]["hone.flow.fork_of"] == source.run_id
    assert run_span["trace_id"] != source.manifest["trace_id"]  # a fork starts its own trace ...
    (link,) = run_span["links"]
    assert link["trace_id"] == source.manifest["trace_id"]  # ... linked to the source's
    assert link["span_id"] == source.manifest["calls"][0]["span_id"]
