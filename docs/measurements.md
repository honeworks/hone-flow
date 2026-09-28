# Measurements and reports

Every step attempt can record how long it took, how big its outputs are and what it used (CPU, memory,
disk, GPU). The numbers go into the step's `metadata.json` and into reports in the run folder, so the slow
and heavy steps of a pipeline can be found from its run folders alone.

Example: [`measurements.py`](../examples/measurements.py).

## Choosing what to measure

`fk.Workflow(..., measure=...)` names what is collected. The default is `("timing", "output_sizes")`;
`measure=()` collects nothing.

| Name | What | Needs |
|---|---|---|
| `timing` | wall time of each step attempt, and the time it waited for the GPU lease | core |
| `output_sizes` | bytes per output file and per step | core |
| `cpu` | system CPU utilization (0 to 1) | extra `metrics` (psutil) |
| `memory` | system memory used and this process's memory (peak, MB) | extra `metrics` |
| `disk` | disk bytes read and written during the step | extra `metrics` |
| `gpu` | GPU memory used (peak, MB, all devices) and utilization (0 to 1) | extra `gpu` (nvidia-ml-py) |

An unknown name raises `fk.WorkflowDefinitionError`; a name whose extra is not installed raises
`fk.HoneFlowError` when the workflow is built ("pip install 'hone-flow[metrics]'"), not in the middle of
a run. With `gpu` but no GPU visible, the GPU keys are left out and `summary.md` says so.

```python
import json
import tempfile
import time
from pathlib import Path

import hone_flow as fk

wf = fk.Workflow("measure_demo", storage=tempfile.mkdtemp())  # timing and output sizes


@wf.step()
def think(prompt: str, seconds: float) -> str:
    time.sleep(seconds)  # stands in for a model call
    return prompt * 100


run = wf.run(
    [fk.Item("fast", {"prompt": "a", "seconds": 0.0}), fk.Item("slow", {"prompt": "b", "seconds": 0.3})]
)
measured = run.steps("think", "slow")[0].measurements
print(measured)  # {'output_sizes': {'total_bytes': 102}, 'timing': {'duration_ms': 301, 'lease_wait_ms': 0}}
assert measured["timing"]["duration_ms"] >= 300
assert measured["output_sizes"] == {"total_bytes": len(json.dumps("b" * 100))}

try:
    fk.Workflow("typo", storage=tempfile.mkdtemp(), measure=("timing", "gpu_hours"))
except fk.WorkflowDefinitionError as exc:
    print(exc)  # unknown measurements ['gpu_hours']; choose from [...]
else:
    raise AssertionError("an unknown measurement is refused")
```

## In each step's metadata

`metadata.json` `measurements` (and `StepRecord.measurements`) holds the enabled measurements of the
latest attempt:

| Key | Content |
|---|---|
| `timing` | `{"duration_ms": ..., "lease_wait_ms": ...}`; a GPU batch's lease wait is charged to its first attempt |
| `output_sizes` | `{"total_bytes": ...}` |
| `system` | the summary of the system measurements, e.g. `{"system.cpu.utilization.mean": 0.41, "system.memory.usage.peak_mb": 5321.0}` |

System measurements are sampled every second while a step runs, plus once when it ends. Keys ending in
`.mean` are averaged, `.peak_mb` keys take the maximum, disk byte counters the last value (counted from
the step's start). The same summary is set as attributes on the step's span, and every sample is a
`metrics.sample` event on it ([records](records.md)). A probe that fails is skipped with a warning; it
never fails the step.

## Reports

At the end of every run, resume and fork call hone-flow (re)writes `reports/` from the step metadata:

| File | Written when | Content |
|---|---|---|
| `reports/timing.json` | `timing` is enabled | `{"steps": [{"step", "item", "status", "attempt", "duration_ms", "lease_wait_ms"}], "total_ms"}` |
| `reports/output_sizes.json` | `output_sizes` is enabled | `{"steps": [{"step", "item", "status", "attempt", "bytes", "files": {name: bytes}}], "total_bytes"}` |
| `reports/system_resources.json` | any of `cpu`, `memory`, `disk`, `gpu` is enabled | `{"steps": [{"step", "item", "status", "attempt", <metric keys>}], "peaks": {<metric>: max}}` |
| `reports/summary.md` | always | status, the count of steps per state, the slowest steps, the largest outputs, resource peaks, warnings |

```python
reports = Path(run.location, "reports")
assert sorted(p.name for p in reports.iterdir()) == ["output_sizes.json", "summary.md", "timing.json"]
timing = json.loads((reports / "timing.json").read_text())
slowest = max(timing["steps"], key=lambda row: row["duration_ms"])
assert slowest["item"] == "slow"
print((reports / "summary.md").read_text())
```

`summary.md` looks like this:

```text
# measure_demo run 20260927T140311Z-3f9a1c

Status: **completed**

## Steps

- done: 2

## Slowest steps

- think/slow: 301 ms
- think/fast: 0 ms

## Largest outputs

- think/fast: 102 bytes
- think/slow: 102 bytes
```

Reports are for people and may change between versions; they are not part of the
[run format](run-format.md). Everything in them comes from the step metadata and the manifest.

## No measurements

```python
quiet = fk.Workflow("quiet", storage=tempfile.mkdtemp(), measure=())


@quiet.step()
def echo(text: str) -> str:
    return text


run = quiet.run([fk.Item("01", {"text": "hi"})])
assert run.steps("echo", "01")[0].measurements == {}
assert [p.name for p in Path(run.location, "reports").iterdir()] == ["summary.md"]
```
