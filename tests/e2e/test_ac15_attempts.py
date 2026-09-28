"""AC-15: every attempt is recorded; only the current result is in output/."""

from pathlib import Path

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e


def test_ac15_attempts(tmp_path: Path) -> None:
    wf = fk.Workflow("attempts", storage=tmp_path)
    broken = {"now": True}

    @wf.step()
    def draft(text: str, ctx: fk.Context) -> str:
        ctx.new_file("log.txt").write_text(f"attempt {ctx.attempt}")
        if broken["now"]:
            raise RuntimeError("model timed out")
        return text

    @wf.gate()
    def check(draft: str) -> str:
        return draft

    run = wf.run([fk.Item("01", {"text": "a"})])
    broken["now"] = False
    run.resume()
    run.reject(step="check", item="01", note="shorter")
    run.resume()
    record = run.steps("draft", "01")[0]
    assert record.attempt == 3
    first, second = record.attempts
    assert (first["attempt"], first["status"], first["note"]) == (1, "failed", None)
    assert "RuntimeError: model timed out" in first["error"]["traceback"]
    assert (second["attempt"], second["status"], second["note"]) == (2, "rejected", "shorter")
    assert second["outputs"]["draft"]["type"] == "json"
    assert all(a["started_at"] and a["ended_at"] for a in record.attempts)

    folder = Path(run.location) / "draft" / "item_01"
    assert (folder / "attempts/1/output/log.txt").read_text() == "attempt 1"  # what the failed attempt wrote
    assert (folder / "attempts/2/output/draft.json").is_file()
    assert sorted(p.name for p in (folder / "output").iterdir()) == ["draft.json"]  # only the current result

    (folder / "attempts/7/output").mkdir(parents=True)  # an attempt metadata.json does not list
    (folder / "attempts/7/output/stray.json").write_text("{}")
    (folder / "inputs/stray.txt").write_text("not a snapshot")
    run.approve(step="check", item="01")
    run.resume()
    assert not (folder / "attempts/7").exists()
    assert not (folder / "inputs/stray.txt").exists()
    assert run.status == "completed"
