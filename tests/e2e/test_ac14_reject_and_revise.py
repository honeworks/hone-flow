"""AC-14: reject-and-revise: the producer reruns with the note as review_note."""

import json
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac14_reject_and_revise(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    wf = song_video(tmp_path / "flows", calls)
    run = wf.run(write_songs(tmp_path), params={"style": "noir"})
    first_seed = run.output("shotlist", "02")["seed"]
    run.approve(step="review_shotlist", item="01")
    run.reject(step="review_shotlist", item="02", note="darker lighting")

    folder = Path(run.location)
    shotlist = run.steps("shotlist", "02")[0]
    assert shotlist.status == "pending"
    assert shotlist.review_note == "darker lighting"
    assert [(a["attempt"], a["status"], a["note"]) for a in shotlist.attempts] == [
        (1, "rejected", "darker lighting")
    ]
    assert (folder / "shotlist/item_02/attempts/1/output/shotlist.json").is_file()
    assert not (folder / "shotlist/item_02/output").exists()
    gate = run.steps("review_shotlist", "02")[0]
    assert (gate.status, gate.attempts[0]["status"]) == ("pending", "rejected")
    assert gate.reviews[-1]["decision"] == "rejected"
    assert run.steps("timeline", "02")[0].status == "done"  # upstream of the producer is untouched
    assert (folder / "review_shotlist/item_02/attempts/1/output/review_shotlist.json").is_file()
    assert not (folder / "review_shotlist/item_02/output").exists()
    assert run.manifest["state"]["shotlist/02"] == "pending"
    assert (run.steps("shotlist", "01")[0].attempt, run.steps("shotlist", "01")[0].attempts) == (1, [])
    (span,) = [s for s in run.spans() if s["attributes"].get("hone.flow.gate.decision") == "rejected"]
    assert span["attributes"]["hone.flow.gate.note"] == "darker lighting"
    assert span["attributes"]["hone.flow.attempt"] == 1

    calls.clear()
    run.resume()
    assert calls == [("shotlist", "02"), ("render", "01")]  # 02's shotlist reruns; 01 renders
    assert run.status == "awaiting_review"  # 02's gate pauses again
    revised = run.output("shotlist", "02")
    assert revised["note"] == "darker lighting"
    assert revised["seed"] != first_seed  # a revision is a new sample
    assert run.steps("shotlist", "02")[0].attempt == 2

    run.reject(step="review_shotlist", item="02", note="even darker")
    run.resume()
    assert run.output("shotlist", "02")["note"] == "even darker"  # the latest note
    assert [a["note"] for a in run.steps("shotlist", "02")[0].attempts] == ["darker lighting", "even darker"]
    run.approve(step="review_shotlist", item="02")
    run.resume()
    assert run.status == "completed"
    gate = run.steps("review_shotlist", "02")[0]
    assert [r["decision"] for r in gate.reviews] == ["rejected", "rejected", "approved"]
    assert json.loads((folder / "review_shotlist/item_02/metadata.json").read_text())["attempt"] == 3


def test_ac14_reject_and_revise_producer_without_review_note(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = fk.Workflow("plain", storage=tmp_path)

    @wf.step()
    def draft(text: str) -> str:
        calls.append("draft")
        return text

    @wf.step()
    def sibling(draft: str) -> str:
        calls.append("sibling")
        return draft

    @wf.gate()
    def check(draft: str) -> str:
        return draft

    run = wf.run([fk.Item("01", {"text": "a"})])
    run.reject(step="check", item="01", note="again")
    assert (
        run.steps("sibling", "01")[0].attempts[0]["status"] == "replaced"
    )  # downstream of the producer reset
    calls.clear()
    run.resume()
    assert calls == ["draft", "sibling"]  # reruns though it takes no review_note
    assert run.status == "awaiting_review"


def test_ac14_reject_and_revise_global_producer(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = fk.Workflow("global_gate", storage=tmp_path)

    @wf.global_step()
    def guide(review_note: str | None = None) -> dict[str, Any]:
        calls.append("guide")
        return {"note": review_note}

    @wf.step()
    def draft(guide: dict[str, Any], ctx: fk.Context) -> str:
        calls.append(f"draft/{ctx.item_id}")
        return str(guide["note"])

    @wf.gate()
    def check(guide: dict[str, Any], draft: str) -> str:
        return draft

    run = wf.run([fk.Item("01"), fk.Item("02")])
    run.reject(step="check", item="01", note="warmer")
    assert [r.status for r in run.steps("check")] == ["pending", "pending"]  # every item's downstream reset
    calls.clear()
    run.resume()
    assert calls == ["guide", "draft/01", "draft/02"]  # the global producer reruns once
    assert run.output("draft", "02") == "warmer"
