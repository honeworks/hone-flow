"""Runs on storage without local paths: MemoryStorage and fsspec memory://."""

import os
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.testing import MemoryStorage
from tests.e2e.song_video import Timeline, song_video, write_songs


@pytest.mark.parametrize("kind", ["memory", "fsspec"])
def test_a_run_on_remote_storage_reads_back_files_and_values(tmp_path: Path, kind: str) -> None:
    storage = MemoryStorage() if kind == "memory" else f"memory://remote-{os.getpid()}-{tmp_path.name}/proj"
    wf = song_video(storage)
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    assert run.status == "awaiting_review"
    assert run.output("timeline", "01") == Timeline(duration=8.0, lines=["first line", "second line"])
    assert run.output("style_guide")["style"] == "noir"
    assert len(run.spans()) == 10  # style_guide, 2 x (timeline, shotlist, gate, render), the run
    prefix = f"song_video/runs/{run.run_id}"
    if isinstance(storage, MemoryStorage):
        keys = storage.list(prefix)
        assert f"{prefix}/manifest.json" in keys
        assert f"{prefix}/timeline/item_01/metadata.json" in keys
        assert f"{prefix}/lease.json" not in keys  # released

    wf2 = fk.Workflow("files", storage=storage)

    @wf2.step()
    def pack(ctx: fk.Context) -> fk.Dir:
        folder = ctx.new_dir("pack")
        (folder / "a.txt").write_text("A")
        return fk.Dir(folder)

    @wf2.step()
    def note(pack: fk.Dir, ctx: fk.Context) -> fk.File:
        path = ctx.new_file("note.md")
        path.write_text((pack.path / "a.txt").read_text() * 2)
        return fk.File(path)

    run2 = wf2.run([fk.Item("01")])
    note_out = run2.output("note", "01", local_dir=tmp_path / "out")
    assert note_out.path == tmp_path / "out" / "note.md"
    assert note_out.path.read_text() == "AA"
    pack_out = run2.output("pack", "01")  # a temporary folder when no local_dir is given
    assert (pack_out.path / "a.txt").read_text() == "A"
    assert wf2.open_run(run2.run_id).status == "completed"
