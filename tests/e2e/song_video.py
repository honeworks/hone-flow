"""The design §3 workflow (design/current.md), shared by the acceptance tests (public API only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

import hone_flow as fk


class Timeline(BaseModel):
    duration: float
    lines: list[str]


def write_songs(folder: Path) -> list[fk.Item]:
    songs = folder / "songs"
    songs.mkdir(parents=True, exist_ok=True)
    (songs / "01.md").write_text("first line\nsecond line\n")
    (songs / "02.md").write_text("only line\n")
    return [
        fk.Item("01", {"lyrics": fk.File(songs / "01.md")}),
        fk.Item("02", {"lyrics": fk.File(songs / "02.md")}),
    ]


def song_video(storage: Any, calls: list[tuple[str, str]] | None = None, **options: Any) -> fk.Workflow:
    """``calls`` records (step, item) of every step call, in order."""
    log = calls if calls is not None else []
    wf = fk.Workflow("song_video", storage=storage, version="1", **options)

    @wf.global_step(version="1")
    def style_guide(style: fk.Param[str]) -> dict[str, Any]:
        log.append(("style_guide", "_global"))
        return {"style": style, "palette": ["black", "amber"]}

    @wf.step(version="1")
    def timeline(item: fk.Item, lyrics: fk.File) -> Timeline:
        log.append(("timeline", item.id))
        lines = lyrics.path.read_text().splitlines()
        return Timeline(duration=len(lines) * 4.0, lines=lines)

    @wf.step(version="1", resources="gpu:ollama", deterministic=False)
    def shotlist(
        timeline: Timeline, style_guide: dict[str, Any], ctx: fk.Context, review_note: str | None = None
    ) -> dict[str, Any]:
        log.append(("shotlist", ctx.item_id))
        shots = [{"line": line, "look": style_guide["style"]} for line in timeline.lines]
        return {"shots": shots, "seed": ctx.seed, "note": review_note}

    @wf.gate()
    def review_shotlist(shotlist: dict[str, Any]) -> dict[str, Any]:
        return shotlist

    @wf.step(version="1")
    def render(review_shotlist: dict[str, Any], ctx: fk.Context) -> fk.File:
        log.append(("render", ctx.item_id))
        out = ctx.new_file("video.txt")
        out.write_text("\n".join(s["line"] for s in review_shotlist["shots"]))
        return fk.File(out)

    return wf
