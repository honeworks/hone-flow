import time

import pytest

from hone_flow import metrics
from hone_flow.metrics import Sampler, summarize


def test_summary_rules() -> None:
    samples = [
        {
            "system.cpu.utilization.mean": 0.2,
            "system.memory.usage.peak_mb": 100.0,
            "system.disk.io.read_bytes": 5.0,
        },
        {
            "system.cpu.utilization.mean": 0.4,
            "system.memory.usage.peak_mb": 50.0,
            "system.disk.io.read_bytes": 9.0,
        },
    ]
    assert summarize(samples) == pytest.approx(
        {
            "system.cpu.utilization.mean": 0.3,
            "system.memory.usage.peak_mb": 100.0,
            "system.disk.io.read_bytes": 9.0,
        }
    )
    assert summarize([]) == {}


def test_sampler_samples_in_a_thread_and_at_the_end() -> None:
    values = iter(range(100))

    def probe() -> dict[str, float]:
        return {"x.mean": float(next(values))}

    with Sampler(0.05, probes=[probe]) as sampler:
        time.sleep(0.3)
    assert len(sampler.events) >= 3
    assert {e["name"] for e in sampler.events} == {"metrics.sample"}
    last = sampler.events[-1]["attributes"]["x.mean"]
    assert sampler.summary()["x.mean"] == pytest.approx(last / 2)


def test_failing_probes_and_no_probes_record_nothing() -> None:
    def broken() -> dict[str, float]:
        raise OSError("sensor gone")

    with Sampler(0.01, probes=[broken]) as sampler:
        time.sleep(0.05)
    assert (sampler.events, sampler.summary()) == ([], {})
    with Sampler(0.01, probes=[]) as idle:
        pass
    assert idle.events == []


def test_psutil_probe_keys() -> None:
    probe = metrics.psutil_probe()
    assert probe is not None
    values = probe()
    assert 0.0 <= values["system.cpu.utilization.mean"] <= 1.0
    assert values["system.memory.usage.peak_mb"] > 0
    assert values["hone.flow.process.memory.peak_mb"] > 0


def test_missing_libraries_skip_their_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metrics, "_import", lambda name: None)
    metrics._nvml_devices.cache_clear()
    try:
        assert metrics.psutil_probe() is None
        assert metrics.nvml_probe() is None
        assert metrics.probes_for(["cpu", "gpu"]) == []
    finally:
        metrics._nvml_devices.cache_clear()


def test_nvml_without_a_driver_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoDriver:
        def nvmlInit(self) -> None:
            raise RuntimeError("NVML Shared Library Not Found")

    monkeypatch.setattr(metrics, "_import", lambda name: NoDriver())
    metrics._nvml_devices.cache_clear()
    try:
        assert metrics.nvml_probe() is None
    finally:
        metrics._nvml_devices.cache_clear()
