"""AC-3: every output kind round-trips through run.output, with readable file names."""

import fractions
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk
from tests.e2e.song_video import Timeline

pytestmark = pytest.mark.e2e

fk.register_serializer(
    fractions.Fraction,
    lambda f: str(f).encode(),
    lambda b: fractions.Fraction(b.decode()),
    name="fraction",
    extension="frac",
)


def test_ac3_serializers_and_outputs(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("# notes\n")
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "p1.txt").write_text("one")
    wf = fk.Workflow("kinds", storage=tmp_path / "flows")
    seen: dict[str, Any] = {}

    @wf.step()
    def model(item: fk.Item) -> Timeline:
        return Timeline(duration=1.5, lines=[item.id])

    @wf.step()
    def data() -> dict[str, Any]:
        return {"b": [1, 2], "a": None}

    @wf.step()
    def clip(ctx: fk.Context) -> fk.File:
        path = ctx.new_file("clip.mp4")
        path.write_bytes(b"\x00\x01video")
        return fk.File(path)

    @wf.step()
    def frames(ctx: fk.Context) -> fk.Dir:
        folder = ctx.new_dir("frames")
        (folder / "0001.png").write_bytes(b"png1")
        (folder / "sub").mkdir()
        (folder / "sub" / "0002.png").write_bytes(b"png2")
        return fk.Dir(folder)

    @wf.step()
    def ratio() -> fractions.Fraction:
        return fractions.Fraction(3, 4)

    @wf.step(outputs=("left", "right"))
    def split(data: dict[str, Any]) -> tuple[str, int]:
        return "L", len(data)

    @wf.step()
    def consume(
        model: Timeline,
        clip: fk.File,
        frames: fk.Dir,
        ratio: fractions.Fraction,
        right: int,
        notes: fk.File,
        pages: fk.Dir,
        mood: str,
    ) -> str:
        seen.update(
            model=model,
            clip=clip.path,
            frames=sorted(p.name for p in frames.path.rglob("*")),
            ratio=ratio,
            right=right,
            notes=notes.path,
            pages=(pages.path / "p1.txt").read_text(),
            mood=mood,
        )
        return "ok"

    item = fk.Item(
        "01", {"notes": fk.File(tmp_path / "notes.md"), "pages": fk.Dir(tmp_path / "pages"), "mood": "calm"}
    )
    run = wf.run([item])
    assert run.status == "completed", run.steps()
    assert run.output("model", "01") == Timeline(duration=1.5, lines=["01"])
    assert run.output("data", "01") == {"a": None, "b": [1, 2]}
    clip_out = run.output("clip", "01")
    assert isinstance(clip_out, fk.File)
    assert clip_out.path.name == "clip.mp4"
    assert clip_out.path.read_bytes() == b"\x00\x01video"
    frames_out = run.output("frames", "01")
    assert isinstance(frames_out, fk.Dir)
    assert frames_out.path.name == "frames"
    assert (frames_out.path / "sub" / "0002.png").read_bytes() == b"png2"
    assert run.output("ratio", "01") == fractions.Fraction(3, 4)
    assert run.output("split", "01", name="left") == "L"
    assert run.output("split", "01", name="right") == 2
    with pytest.raises(fk.HoneFlowError, match="pass name="):
        run.output("split", "01")

    folder = Path(run.location)
    assert (folder / "model/item_01/output/model.json").is_file()
    assert (folder / "ratio/item_01/output/ratio.frac").read_text() == "3/4"
    assert (folder / "split/item_01/output/left.json").is_file()
    assert (folder / "split/item_01/output/right.json").is_file()
    assert (folder / "frames/item_01/output/frames/sub/0002.png").is_file()
    inputs = sorted(
        p.relative_to(folder / "consume/item_01/inputs").as_posix()
        for p in (folder / "consume/item_01/inputs").rglob("*")
        if p.is_file()
    )
    assert inputs == [
        "clip.mp4",
        "frames/0001.png",
        "frames/sub/0002.png",
        "model.json",
        "mood.json",
        "notes.md",
        "pages/p1.txt",
        "ratio.frac",
        "right.json",
    ]
    assert seen["model"] == Timeline(duration=1.5, lines=["01"])
    assert seen["clip"].name == "clip.mp4"
    assert seen["frames"] == ["0001.png", "0002.png", "sub"]
    assert seen["ratio"] == fractions.Fraction(3, 4)
    assert (seen["right"], seen["mood"]) == (2, "calm")
    assert seen["notes"].name == "notes.md"
    assert seen["pages"] == "one"
    assert not seen["clip"].exists()  # the local work folder is removed at the end of the call
    assert run.steps("model", "01")[0].outputs["model"]["type"] == "pydantic:tests.e2e.song_video:Timeline"
    assert run.steps("ratio", "01")[0].outputs["ratio"]["type"] == "custom:fraction"


def test_ac3_serializers_and_outputs_errors(tmp_path: Path) -> None:
    wf = fk.Workflow("bad", storage=tmp_path)

    @wf.step()
    def weird(x: int) -> object:
        return object()

    @wf.step(outputs=("a", "b"))
    def pair(x: int) -> tuple[int, int]:
        return (x,)  # type: ignore[return-value]

    @wf.step()
    def ghost(x: int) -> fk.File:
        return fk.File(tmp_path / "missing.txt")

    run = wf.run([fk.Item("01", {"x": 1})])
    assert run.status == "failed"
    errors = {r.step: r.error["message"] for r in run.steps() if r.error}
    assert "cannot serialize a value of type object" in errors["weird"]
    assert "return a tuple of that length" in errors["pair"]
    assert "does not exist" in errors["ghost"]
    with pytest.raises(fk.OutputNotFound, match="failed"):
        run.output("weird", "01")
