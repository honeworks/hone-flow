"""Params and the step context: settings, seeds, files and folders, several outputs.

What: ``fk.Param[T]`` for run-level settings, ``fk.Context`` for everything about the current step
(ids, a stable ``seed``, a work folder), ``fk.File`` / ``fk.Dir`` outputs, and ``outputs=(...)`` for a
step that returns several values.

How: annotate a parameter ``fk.Param[str]`` and pass ``params={...}`` to ``wf.run``; declare
``ctx: fk.Context`` to get ``ctx.seed``, ``ctx.new_file(name)`` and ``ctx.new_dir(name)``; return
``fk.File(path)`` / ``fk.Dir(path)`` to store files; with ``@wf.step(outputs=("a", "b"))`` return a
tuple and let later steps name ``a`` or ``b``.

Why: only the params a step declares reach it (and are recorded in its metadata), so it is clear what
influenced each result. ``ctx.seed`` is derived from the run seed, item and step: the same on resume and
retry, different per item and step, so sampling is reproducible.
"""

import tempfile
from pathlib import Path

import hone_flow as fk

wf = fk.Workflow("params_demo", storage=tempfile.mkdtemp())


@wf.step(outputs=("title", "words"))
def split(text: str) -> tuple[str, list[str]]:
    first, *rest = text.split()
    return first.title(), rest


@wf.step()
def cover(title: str, style: fk.Param[str], ctx: fk.Context) -> fk.File:
    path = ctx.new_file("cover.txt")
    path.write_text(f"{title} in {style} (seed {ctx.seed})")
    return fk.File(path)


@wf.step()
def frames(words: list[str], ctx: fk.Context, fps: fk.Param[int] = 12) -> fk.Dir:
    folder = ctx.new_dir("frames")
    for i, word in enumerate(words):
        (folder / f"{i:04d}.txt").write_text(word)
    (folder / "fps.txt").write_text(str(fps))
    return fk.Dir(folder)


run = wf.run([fk.Item("01", {"text": "moon rises over water"})], params={"style": "noir"}, seed=42)
cover_file = run.output("cover", "01")
frame_dir = run.output("frames", "01")
print(cover_file.path.name, "->", cover_file.path.read_text())
print(frame_dir.path.name, "->", sorted(p.name for p in frame_dir.path.iterdir()))
record = run.steps("cover", "01")[0]
print("cover params:", record.params, "| title:", run.output("split", "01", name="title"))

assert run.output("split", "01", name="words") == ["rises", "over", "water"]
assert record.params == {"style": "noir"}  # only what the step declares
assert run.steps("frames", "01")[0].params == {"fps": 12}  # defaults are recorded too
assert isinstance(frame_dir, fk.Dir) and (frame_dir.path / "fps.txt").read_text() == "12"
assert Path(run.location, "cover/item_01/output/cover.txt").is_file()
