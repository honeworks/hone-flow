"""AC-19: measurements in metadata and reports/."""

import json
import time
from pathlib import Path

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e


def flow(storage: Path, **options: object) -> fk.Workflow:
    wf = fk.Workflow("measured", storage=storage, **options)  # type: ignore[arg-type]

    @wf.step()
    def write(text: str) -> str:
        time.sleep(0.05)
        return text * 10

    return wf


def reports(run: fk.Run) -> list[str]:
    return sorted(p.name for p in (Path(run.location) / "reports").iterdir())


def test_ac19_measurements_and_reports_default(tmp_path: Path) -> None:
    run = flow(tmp_path).run([fk.Item("01", {"text": "abc"}), fk.Item("02", {"text": "de"})])
    assert run.manifest["measure"] == ["timing", "output_sizes"]
    record = run.steps("write", "01")[0]
    assert record.measurements["timing"]["duration_ms"] >= 50
    assert record.measurements["timing"]["lease_wait_ms"] == 0
    assert record.measurements["output_sizes"] == {"total_bytes": len(json.dumps("abc" * 10))}
    assert reports(run) == ["output_sizes.json", "summary.md", "timing.json"]
    folder = Path(run.location) / "reports"
    timing = json.loads((folder / "timing.json").read_text())
    assert [(r["step"], r["item"]) for r in timing["steps"]] == [("write", "01"), ("write", "02")]
    sizes = json.loads((folder / "output_sizes.json").read_text())
    assert sizes["total_bytes"] == len(json.dumps("abc" * 10)) + len(json.dumps("de" * 10))
    assert sizes["steps"][0]["files"] == {"write.json": len(json.dumps("abc" * 10))}
    summary = (folder / "summary.md").read_text()
    assert "Status: **completed**" in summary
    assert "- done: 2" in summary
    assert "## Slowest steps" in summary
    assert "## Largest outputs" in summary


def test_ac19_measurements_and_reports_none(tmp_path: Path) -> None:
    run = flow(tmp_path, measure=()).run([fk.Item("01", {"text": "abc"})])
    assert run.steps("write", "01")[0].measurements == {}
    assert reports(run) == ["summary.md"]
    summary = (Path(run.location) / "reports" / "summary.md").read_text()
    assert "Slowest steps" not in summary
    assert "Largest outputs" not in summary


def test_ac19_measurements_and_reports_selects_keys(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    run = flow(tmp_path, measure=("memory",)).run([fk.Item("01", {"text": "abc"})])
    system = run.steps("write", "01")[0].measurements["system"]
    assert system
    assert all(".memory." in key for key in system)  # no cpu or disk keys


def test_ac19_measurements_and_reports_system(tmp_path: Path) -> None:
    pytest.importorskip("psutil")
    wf = fk.Workflow("system", storage=tmp_path, measure=("cpu", "memory", "disk"))

    @wf.step()
    def busy(seconds: float) -> float:
        time.sleep(seconds)
        return seconds

    run = wf.run([fk.Item("01", {"seconds": 2.0})])
    assert reports(run) == ["summary.md", "system_resources.json"]
    system = run.steps("busy", "01")[0].measurements["system"]
    assert {
        "system.cpu.utilization.mean",
        "system.memory.usage.peak_mb",
        "system.disk.io.write_bytes",
    } <= set(system)
    assert "timing" not in run.steps("busy", "01")[0].measurements
    (span,) = [s for s in run.spans() if s["name"] == "hone.flow.step"]
    assert span["attributes"]["system.memory.usage.peak_mb"] == system["system.memory.usage.peak_mb"]
    samples = [e for e in span["events"] if e["name"] == "metrics.sample"]
    assert len(samples) >= 2  # one per second while the 2 s step ran, and one at its end
    resources = json.loads((Path(run.location) / "reports" / "system_resources.json").read_text())
    assert resources["peaks"]["system.memory.usage.peak_mb"] == system["system.memory.usage.peak_mb"]
    assert "## Resource peaks" in (Path(run.location) / "reports" / "summary.md").read_text()


def test_ac19_measurements_and_reports_bad_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(fk.WorkflowDefinitionError, match=r"unknown measurements \['gpus'\]"):
        fk.Workflow("x", storage=tmp_path, measure=("gpus",))
    import sys

    monkeypatch.setitem(sys.modules, "psutil", None)
    with pytest.raises(fk.HoneFlowError, match=r"pip install 'hone-flow\[metrics\]'"):
        fk.Workflow("x", storage=tmp_path, measure=("cpu",))
    monkeypatch.setitem(sys.modules, "pynvml", None)
    with pytest.raises(fk.HoneFlowError, match=r"pip install 'hone-flow\[gpu\]'"):
        fk.Workflow("x", storage=tmp_path, measure=("gpu",))
