"""The metrics sampler reads GPU memory and utilization when NVML is available (extra 'gpu')."""

import time

import pytest

from hone_flow.metrics import Sampler, nvml_probe

pytestmark = pytest.mark.gpu


def test_gpu_metrics_are_sampled(gpu_lock: None) -> None:
    pynvml = pytest.importorskip("pynvml", reason="GPU metrics need the 'gpu' extra (nvidia-ml-py)")
    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError as exc:
        pytest.skip(f"NVML not usable: {exc}")
    probe = nvml_probe()
    assert probe is not None
    with Sampler(0.2, [probe]) as sampler:
        time.sleep(0.5)
    summary = sampler.summary()
    assert summary["hone.flow.gpu.memory.peak_mb"] > 0
    assert 0.0 <= summary["hone.flow.gpu.utilization.mean"] <= 1.0
