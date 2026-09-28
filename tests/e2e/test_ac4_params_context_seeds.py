"""AC-4: params, the step context and seeds."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e


def seeded_workflow(storage: Path, seen: dict[tuple[str, str], Any]) -> fk.Workflow:
    wf = fk.Workflow("seeds", storage=storage)

    @wf.global_step()
    def palette(ctx: fk.Context) -> str:
        seen[("palette", ctx.item_id)] = ctx.seed
        return "amber"

    @wf.step()
    def draft(text: str, style: fk.Param[str], ctx: fk.Context) -> str:
        seen[("draft", ctx.item_id)] = ctx.seed
        seen[("ctx", ctx.item_id)] = (ctx.run_id, ctx.trace_id, ctx.step, ctx.attempt)
        return f"{text}/{style}"

    @wf.step(deterministic=False)
    def polish(draft: str, palette: str, ctx: fk.Context, strength: fk.Param[int] = 2) -> str:
        seen[("polish", ctx.item_id)] = ctx.seed
        seen[("strength", ctx.item_id)] = strength
        return draft + "!" * strength

    return wf


def expected_seed(run_seed: int, item: str, step: str) -> int:
    return int(hashlib.sha256(f"{run_seed}:{item}:{step}".encode()).hexdigest()[:8], 16)


def test_ac4_params_context_seeds(tmp_path: Path) -> None:
    seen: dict[tuple[str, str], Any] = {}
    wf = seeded_workflow(tmp_path, seen)
    items = [fk.Item("01", {"text": "a"}), fk.Item("02", {"text": "b"})]
    run = wf.run(items, params={"style": "noir", "unused": 1}, seed=7)
    assert run.status == "completed"
    assert run.output("polish", "02") == "b/noir!!"
    assert seen[("strength", "01")] == 2  # a param default applies when the run does not set it
    records = {(r.step, r.item): r for r in run.steps()}
    assert records[("draft", "01")].params == {"style": "noir"}  # only the params a step declares
    assert records[("polish", "01")].params == {"strength": 2}
    assert records[("palette", None)].params == {}
    assert run.manifest["params"] == {"style": "noir", "unused": 1}

    assert seen[("draft", "01")] == expected_seed(7, "01", "draft")
    assert seen[("palette", "_global")] == expected_seed(7, "_global", "palette")
    seeds = {seen[(s, i)] for s in ("draft", "polish") for i in ("01", "02")}
    assert len(seeds) == 4  # different per item and step
    assert records[("draft", "01")].attempt == 1
    run_id, trace_id, step, attempt = seen[("ctx", "01")]
    assert (run_id, trace_id, step, attempt) == (run.run_id, run.manifest["trace_id"], "draft", 1)

    again: dict[tuple[str, str], Any] = {}
    seeded_workflow(tmp_path, again).run(items, params={"style": "noir"}, seed=7)
    assert again[("draft", "01")] == seen[("draft", "01")]  # the same run seed gives the same step seeds

    folder = Path(run.location)
    assert json.loads((folder / "polish/item_01/metadata.json").read_text())["deterministic"] is False
    assert json.loads((folder / "draft/item_01/metadata.json").read_text())["deterministic"] is True
    assert run.manifest["steps"][2]["deterministic"] is False
    spans = [
        s for s in run.spans() if s["name"] == "hone.flow.step" and s["attributes"]["hone.step"] == "polish"
    ]
    assert spans
    assert all(s["attributes"]["hone.flow.deterministic"] is False for s in spans)


def test_ac4_params_context_seeds_default_run_seed_is_zero(tmp_path: Path) -> None:
    seen: dict[tuple[str, str], Any] = {}
    seeded_workflow(tmp_path, seen).run([fk.Item("01", {"text": "a"})], params={"style": "x"})
    assert seen[("draft", "01")] == expected_seed(0, "01", "draft")


def test_ac4_params_context_seeds_param_errors(tmp_path: Path) -> None:
    wf = seeded_workflow(tmp_path, {})
    items = [fk.Item("01", {"text": "a"})]
    with pytest.raises(fk.WorkflowDefinitionError, match="needs the run param 'style'"):
        wf.run(items)
    with pytest.raises(fk.WorkflowDefinitionError, match="JSON-compatible"):
        wf.run(items, params={"style": object()})
    with pytest.raises(fk.HoneFlowError, match=r"JSON-compatible values, fk\.File or fk\.Dir"):
        wf.run([fk.Item("01", {"text": object()})], params={"style": "x"})
    assert not (tmp_path / "seeds" / "runs").exists() or not list((tmp_path / "seeds" / "runs").iterdir())
