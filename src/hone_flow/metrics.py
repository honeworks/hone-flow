"""System metrics sampled in a thread while a step runs (design §4.10, docs/records.md).

CPU, memory and disk I/O come from ``psutil`` (extra ``metrics``), GPU memory and utilization from NVML
(``nvidia-ml-py``, extra ``gpu``). A missing library means those keys are skipped, never faked.
Each sample is a ``metrics.sample`` event; the step span gets the summary: ``*.mean`` keys are averaged,
``*.peak_mb`` keys take the maximum, disk byte counters (counted from the step start) take the last value.
"""

from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable, Sequence
from types import ModuleType, TracebackType
from typing import Any

from hone_flow._tracing import iso_now
from hone_flow.errors import HoneFlowError, WorkflowDefinitionError

logger = logging.getLogger("hone_flow")

Probe = Callable[[], dict[str, float]]
MB = 1024 * 1024


def _import(name: str) -> ModuleType | None:
    try:
        return __import__(name)
    except ImportError:
        return None


def psutil_probe() -> Probe | None:
    """CPU utilization (0-1, whole system), memory (system and this process) and disk I/O since start."""
    psutil: Any = _import("psutil")
    if psutil is None:
        return None
    process = psutil.Process()
    psutil.cpu_percent(interval=None)  # the first call only starts the measurement
    start = psutil.disk_io_counters()

    def probe() -> dict[str, float]:
        values = {
            "system.cpu.utilization.mean": psutil.cpu_percent(interval=None) / 100,
            "system.memory.usage.peak_mb": psutil.virtual_memory().used / MB,
            "hone.flow.process.memory.peak_mb": process.memory_info().rss / MB,
        }
        disk = psutil.disk_io_counters()
        if start is not None and disk is not None:
            values["system.disk.io.read_bytes"] = disk.read_bytes - start.read_bytes
            values["system.disk.io.write_bytes"] = disk.write_bytes - start.write_bytes
        return values

    return probe


@functools.cache
def _nvml_devices() -> tuple[Any, list[Any]] | None:
    """(pynvml module, device handles), initialized once per process; ``None`` without a usable GPU."""
    nvml: Any = _import("pynvml")
    if nvml is None:
        return None
    try:
        nvml.nvmlInit()
        handles = [nvml.nvmlDeviceGetHandleByIndex(i) for i in range(nvml.nvmlDeviceGetCount())]
    except Exception:  # no driver or no device: GPU metrics are skipped
        return None
    return (nvml, handles) if handles else None


def nvml_probe() -> Probe | None:
    """GPU memory used (MB, all devices) and utilization (0-1, mean over devices)."""
    devices = _nvml_devices()
    if devices is None:
        return None
    nvml, handles = devices

    def probe() -> dict[str, float]:
        used = sum(nvml.nvmlDeviceGetMemoryInfo(h).used for h in handles)
        busy = [nvml.nvmlDeviceGetUtilizationRates(h).gpu / 100 for h in handles]
        return {
            "hone.flow.gpu.memory.peak_mb": used / MB,
            "hone.flow.gpu.utilization.mean": sum(busy) / len(busy),
        }

    return probe


def summarize(samples: list[dict[str, float]]) -> dict[str, float]:
    """Summary attributes of a list of samples (see the module docstring)."""
    summary: dict[str, float] = {}
    for key in dict.fromkeys(k for s in samples for k in s):
        values = [s[key] for s in samples if key in s]
        if key.endswith(".mean"):
            summary[key] = sum(values) / len(values)
        elif key.endswith(".peak_mb"):
            summary[key] = max(values)
        else:
            summary[key] = values[-1]
    return summary


class Sampler:
    """Samples every ``interval`` seconds in a thread, and once more when the block ends.

    >>> with Sampler(0.1, probes=[lambda: {"x.mean": 1.0}]) as sampler:
    ...     pass
    >>> sampler.summary()
    {'x.mean': 1.0}
    """

    def __init__(self, interval: float, probes: list[Probe]) -> None:
        self.interval = interval
        self.probes = probes
        self.events: list[dict[str, Any]] = []
        self._failed: set[Probe] = set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="hone-flow-metrics", daemon=True)

    def __enter__(self) -> Sampler:
        if self.probes:
            self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self.probes:
            self._stop.set()
            self._thread.join()
            self._sample()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._sample()

    def _sample(self) -> None:
        values: dict[str, float] = {}
        for probe in self.probes:
            try:
                values |= probe()
            except Exception:  # a probe failing mid-run must not fail the step
                if probe not in self._failed:
                    self._failed.add(probe)
                    logger.warning("metrics probe %r failed; its metrics are skipped", probe, exc_info=True)
        if values:
            self.events.append({"name": "metrics.sample", "time": iso_now(), "attributes": values})

    def summary(self) -> dict[str, float]:
        return summarize([e["attributes"] for e in self.events])


SAMPLE_INTERVAL_S = 1.0
SYSTEM_MEASURES: dict[str, tuple[str, ...]] = {  # measure name -> the metric keys it selects
    "cpu": ("system.cpu.",),
    "memory": ("system.memory.", "hone.flow.process.memory."),
    "disk": ("system.disk.",),
    "gpu": ("hone.flow.gpu.",),
}
MEASURES = ("timing", "output_sizes", *SYSTEM_MEASURES)
PSUTIL_MEASURES = {"cpu", "memory", "disk"}


def check_measure(measure: Sequence[str]) -> tuple[str, ...]:
    """The measurements a workflow collects; unknown names and missing extras fail at construction."""
    unknown = [name for name in measure if name not in MEASURES]
    if unknown:
        raise WorkflowDefinitionError(f"unknown measurements {unknown}; choose from {list(MEASURES)}")
    if PSUTIL_MEASURES & set(measure) and _import("psutil") is None:
        raise HoneFlowError("measuring cpu, memory or disk needs psutil: pip install 'hone-flow[metrics]'")
    if "gpu" in measure and _import("pynvml") is None:
        raise HoneFlowError("measuring the GPU needs nvidia-ml-py: pip install 'hone-flow[gpu]'")
    return tuple(measure)


def probes_for(measure: Sequence[str]) -> list[Probe]:
    """Fresh probes (disk counters start now) that report only the keys the measurements select."""
    prefixes = tuple(p for name in measure for p in SYSTEM_MEASURES.get(name, ()))
    if not prefixes:
        return []
    found = [
        psutil_probe() if {"cpu", "memory", "disk"} & set(measure) else None,
        nvml_probe() if "gpu" in measure else None,
    ]

    def selected(probe: Probe) -> Probe:
        return lambda: {k: v for k, v in probe().items() if k.startswith(prefixes)}

    return [selected(probe) for probe in found if probe is not None]
