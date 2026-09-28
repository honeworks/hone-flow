"""Measurements and reports: how long each step took, how big its outputs are, what it used.

What: ``Workflow(measure=...)`` chooses what is recorded per step attempt: ``"timing"`` and
``"output_sizes"`` (the default), ``"cpu"``, ``"memory"``, ``"disk"`` (extra ``metrics``) and ``"gpu"``
(extra ``gpu``). Results go into each step's ``metadata.json`` (``measurements``) and into
``reports/`` (``timing.json``, ``output_sizes.json``, ``system_resources.json``, ``summary.md``).

How: pass the names you want (``measure=()`` for none). System measurements are sampled every second
while a step runs; their summary is on the step's span and in its metadata. Reports are rewritten at
the end of every run / resume / fork call.

Why: to find the slow and the heavy steps of a pipeline from its run folders, without a monitoring
service. A missing extra fails when the workflow is built, not in the middle of a run.
"""

import json
import tempfile
import time
from pathlib import Path

import hone_flow as fk

measure = ("timing", "output_sizes")
try:
    import psutil  # noqa: F401 - only to see whether the `metrics` extra is installed
except ImportError:
    print("psutil is missing (pip install 'hone-flow[metrics]'): timing and sizes only")
else:
    measure += ("cpu", "memory")

wf = fk.Workflow("measure_demo", storage=tempfile.mkdtemp(), measure=measure)


@wf.step()
def think(prompt: str, seconds: float) -> str:
    time.sleep(seconds)  # stands in for a model call
    return prompt * 100


run = wf.run(
    [fk.Item("fast", {"prompt": "a", "seconds": 0.1}), fk.Item("slow", {"prompt": "b", "seconds": 1.2})]
)
folder = Path(run.location) / "reports"
print(sorted(p.name for p in folder.iterdir()))
print(run.steps("think", "slow")[0].measurements)
print((folder / "summary.md").read_text())

timing = json.loads((folder / "timing.json").read_text())
slowest = max(timing["steps"], key=lambda row: row["duration_ms"])
assert slowest["item"] == "slow" and slowest["duration_ms"] >= 1200
assert json.loads((folder / "output_sizes.json").read_text())["total_bytes"] == 2 * len(json.dumps("a" * 100))
if "cpu" in measure:
    assert "system.memory.usage.peak_mb" in run.steps("think", "slow")[0].measurements["system"]
