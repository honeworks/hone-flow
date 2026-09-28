"""AC-33: a revised producer receives its previous output (design change 0006)."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import hone_flow as fk

pytestmark = pytest.mark.e2e


class Scenes(BaseModel):
    code: dict[str, str]


def flow(storage: Path, seen: list[Any], ctx_seen: list[Any]) -> fk.Workflow:
    wf = fk.Workflow("shorts", storage=storage)

    @wf.step()
    def code(
        scenes: list[str], ctx: fk.Context, review_note: str | None = None, previous: Scenes | None = None
    ) -> Scenes:
        seen.append(previous)
        ctx_seen.append(ctx.previous_output())
        if previous is None:
            return Scenes(code={s: f"draft {s}" for s in scenes})
        failed = (review_note or "").split(",")
        return Scenes(code={s: (f"fixed {s}" if s in failed else v) for s, v in previous.code.items()})

    @wf.gate()
    def render_check(code: Scenes) -> Scenes:
        return code

    @wf.step()
    def render(render_check: Scenes, previous: str | None = None) -> str:
        seen.append(("render", previous))
        return " | ".join(render_check.code.values())

    return wf


def test_ac33_previous_output_on_revise(tmp_path: Path) -> None:
    seen: list[Any] = []
    ctx_seen: list[Any] = []
    wf = flow(tmp_path / "flows", seen, ctx_seen)
    run = wf.run([fk.Item("ep1", {"scenes": ["s1", "s2", "s3"]})])
    assert seen == [None] and ctx_seen == [None]  # a first attempt has no previous output

    run.reject(step="render_check", item="ep1", note="s2")
    run.resume()
    first = Scenes(code={"s1": "draft s1", "s2": "draft s2", "s3": "draft s3"})
    assert seen[-1] == first  # the rejected output, loaded with the step's type
    assert ctx_seen[-1] == first
    assert run.output("code", "ep1").code == {"s1": "draft s1", "s2": "fixed s2", "s3": "draft s3"}

    run.reject(step="render_check", item="ep1", note="s3")
    run.resume()
    assert seen[-1].code["s2"] == "fixed s2"  # the latest rejected attempt, not the first
    run.approve(step="render_check", item="ep1")
    run.resume()
    assert seen[-1] == ("render", None)
    assert run.output("render", "ep1") == "draft s1 | fixed s2 | fixed s3"

    forked = run.fork(refresh=("code",))
    assert forked.status == "awaiting_review"
    assert seen[-1] is None  # a fork reruns from scratch


def test_ac33_replaced_outputs_and_several_outputs(tmp_path: Path) -> None:
    got: list[Any] = []
    wf = fk.Workflow("multi", storage=tmp_path / "flows")

    @wf.step(outputs=("lyrics", "title"))
    def write(topic: str, previous: tuple[str, str] | None = None) -> tuple[str, str]:
        got.append(previous)
        return (f"la la {topic}", topic.title()) if previous is None else (previous[0] + "!", previous[1])

    @wf.gate()
    def check(lyrics: str) -> str:
        return lyrics

    @wf.step()
    def sing(check: str, title: str, previous: str | None = None) -> str:
        got.append(("sing", previous))
        return f"{title}: {check}"

    @wf.step()
    def announce(title: str, previous: str | None = None) -> str:
        got.append(("announce", previous))
        return f"now: {title}"

    run = wf.run([fk.Item("a", {"topic": "rain"})])
    assert got == [None, ("announce", None)]
    run.reject(step="check", item="a", note="louder")  # announce (downstream of write) is replaced
    run.resume()
    assert got[2] == ("la la rain", "Rain")  # several outputs arrive as a tuple
    assert got[3] == ("announce", "now: Rain")  # a replaced output is passed on too
    run.approve(step="check", item="a")
    run.resume()
    assert run.output("sing", "a") == "Rain: la la rain!"
    assert got[-1] == ("sing", None)
