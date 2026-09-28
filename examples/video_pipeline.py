"""A clip pipeline: the OneShotStudio song-to-video shape, end to end, with fake models.

What: songs (lyrics files) become videos through a style guide shared by all songs, a timeline, an LLM
shot list (Ollama), a human review of the shot list, image prompts (Ollama), frames (ComfyUI) and a
video (ffmpeg). The model calls are fakes; the shape, the storage and the controls are the real thing.

How: one ``fk.Workflow`` with a ``global_step``, per-song ``step``s tagged ``gpu:ollama`` /
``gpu:comfyui``, a ``gate`` after the shot list, ``fk.File`` / ``fk.Dir`` outputs, a run that pauses for
review, ``reject`` with a note that the shot list step reads as ``review_note``, ``resume``, and a
``fork(refresh=("images",))`` to try new frames without redoing the text steps.

Why: this is the set of problems hone-flow was built for (design §8): "done" flags that ignored changed
inputs (the fork diff checks them), no way to see what a run used (input snapshots), step lists copied
across runners (one DAG), review notes nothing read (``review_note``), full reruns after a crash (per-step
commits), and hand-made GPU batching (``gpu:`` tags batch the Ollama steps, then the ComfyUI steps).
"""

import tempfile
from pathlib import Path
from typing import Any

import hone_flow as fk
from hone_flow.testing import FakeGpuLease

tmp = Path(tempfile.mkdtemp(prefix="hone-flow-example-"))
songs = tmp / "songs"
songs.mkdir()
(songs / "01.md").write_text("city lights\nlate train\n")
(songs / "02.md").write_text("open sea\n")
gpu = FakeGpuLease()  # in production: gpu="hone_models" or fk.FileLockGpuLease()
wf = fk.Workflow("song_video", storage=tmp / "flows", gpu=gpu)


@wf.global_step()
def style_guide(style: fk.Param[str]) -> dict[str, Any]:
    return {"style": style, "palette": ["black", "amber"]}


@wf.step()
def timeline(lyrics: fk.File) -> list[dict[str, Any]]:
    lines = lyrics.path.read_text().splitlines()
    return [{"start": 4.0 * i, "line": line} for i, line in enumerate(lines)]


@wf.step(resources="gpu:ollama", vram_gb=6.0, deterministic=False)
def shotlist(
    timeline: list[dict[str, Any]],
    style_guide: dict[str, Any],
    ctx: fk.Context,
    review_note: str | None = None,
) -> list[dict[str, Any]]:
    mood = "dark" if review_note and "dark" in review_note else style_guide["style"]
    return [
        {"at": cue["start"], "shot": f"{cue['line']} ({mood}, seed {ctx.seed % 100})"} for cue in timeline
    ]


@wf.gate()
def review_shotlist(shotlist: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return shotlist


@wf.step(resources="gpu:ollama", vram_gb=6.0)
def prompts(review_shotlist: list[dict[str, Any]], style_guide: dict[str, Any]) -> list[str]:
    return [f"{shot['shot']}, palette {'/'.join(style_guide['palette'])}" for shot in review_shotlist]


@wf.step(resources="gpu:comfyui", vram_gb=7.5, deterministic=False)
def images(prompts: list[str], ctx: fk.Context) -> fk.Dir:
    frames = ctx.new_dir("frames")
    for i, prompt in enumerate(prompts):
        (frames / f"{i:04d}.png").write_text(f"image of {prompt} #{ctx.seed % 1000}")  # a fake PNG
    return fk.Dir(frames)


@wf.step()
def video(images: fk.Dir, timeline: list[dict[str, Any]], ctx: fk.Context) -> fk.File:
    out = ctx.new_file("clip.mp4")
    frames = sorted(images.path.iterdir())
    out.write_text(f"{len(frames)} frames over {4.0 * len(timeline)} s")  # a fake ffmpeg
    return fk.File(out)


items = [fk.Item(path.stem, {"lyrics": fk.File(path)}) for path in sorted(songs.glob("*.md"))]
run = wf.run(items, params={"style": "silhouette"})
print("1.", run.status, "- waiting for someone to read the shot lists")

run.approve(step="review_shotlist", item="01")
run.reject(step="review_shotlist", item="02", note="make the sea dark")
run.resume()
print("2.", run.status, "- 01 rendered, 02 revised:", run.output("shotlist", "02")[0]["shot"])
run.approve(step="review_shotlist", item="02")
run.resume()
print("3.", run.status, "-", run.output("video", "01").path.read_text())

leases = [call for call in gpu.calls if call[0] == "enter"]
print("   GPU leases:", leases)
new = run.fork(refresh=("images",))  # new frames; lyrics, shot lists and reviews are reused
reran = [f"{row['step']}/{row['item']}" for row in new.manifest["fork_of"]["plan"] if row["action"] == "run"]
print("4. fork", new.run_id, "reran", reran)

assert run.status == "completed" and new.status == "completed"
assert "dark" in run.output("shotlist", "02")[0]["shot"]
assert run.steps("review_shotlist", "02")[0].reviews[0]["note"] == "make the sea dark"
assert reran == ["images/01", "images/02", "video/01", "video/02"]
assert new.steps("review_shotlist", "01")[0].labels == ["reused", "approved"]
assert (Path(run.location) / "images/item_01/output/frames/0001.png").is_file()
assert (Path(run.location) / "timeline/item_01/inputs/lyrics.md").read_text() == "city lights\nlate train\n"
