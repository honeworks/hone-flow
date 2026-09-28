"""AC-37: seeds and who decided, in the records (design change 0010)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import hone_flow as fk
from hone_flow.cli import app

pytestmark = pytest.mark.e2e


def make(storage: Path) -> fk.Workflow:
    wf = fk.Workflow("seeded", storage=storage)

    @wf.step(deterministic=False)
    def draft(text: str, ctx: fk.Context) -> dict[str, object]:
        return {"text": text, "seed": ctx.seed}

    @wf.gate()
    def check(draft: dict[str, object]) -> dict[str, object]:
        return draft

    return wf


def test_ac37_seeds_on_spans(tmp_path: Path) -> None:
    run = make(tmp_path / "flows").run([fk.Item("a", {"text": "x"})], seed=42)
    spans = run.spans()
    (run_span,) = [s for s in spans if s["name"] == "hone.flow.run"]
    assert run_span["attributes"]["hone.flow.seed"] == 42
    (draft_span,) = [s for s in spans if s["attributes"].get("hone.step") == "draft"]
    assert draft_span["attributes"]["hone.flow.seed"] == run.output("draft", "a")["seed"]  # ctx.seed


def test_ac37_person_or_automated_decisions(tmp_path: Path) -> None:
    run = make(tmp_path / "flows").run([fk.Item("a", {"text": "x"}), fk.Item("b", {"text": "y"})])
    run.approve(step="check", item="a", actor="render-check", automated=True)
    run.reject(step="check", item="b", note="again", actor="ana")
    reviews = {r.item: r.reviews for r in run.steps("check")}
    assert (reviews["a"][0]["actor"], reviews["a"][0]["actor_kind"]) == ("render-check", "automated")
    assert (reviews["b"][0]["actor"], reviews["b"][0]["actor_kind"]) == ("ana", "person")  # the default
    decisions = [s for s in run.spans() if "hone.flow.gate.decision" in s["attributes"]]
    assert [s["attributes"]["hone.flow.gate.actor_kind"] for s in decisions] == ["automated", "person"]

    make(tmp_path / "flows").open_run(run.run_id).resume()
    result = CliRunner().invoke(
        app,
        [
            *("approve", run.run_id, "--step", "check", "--item", "b", "--automated"),
            *("--storage", str(tmp_path / "flows"), "--name", "seeded", "--json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["decision"] == "approved"
    assert run.steps("check", "b")[0].reviews[-1]["actor_kind"] == "automated"


def test_ac37_reviews_without_actor_kind_read_as_person(tmp_path: Path) -> None:
    run = make(tmp_path / "flows").run([fk.Item("a", {"text": "x"})])
    run.approve(step="check", item="a")
    meta_path = Path(run.location, "check/item_a/metadata.json")
    data = json.loads(meta_path.read_text())
    del data["reviews"][0]["actor_kind"]  # a run made before the key existed
    meta_path.chmod(0o644)
    meta_path.write_text(json.dumps(data))
    assert run.steps("check", "a")[0].reviews[0]["actor_kind"] == "person"
