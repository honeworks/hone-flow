"""Progress while a run runs: INFO log lines and an ``on_event`` callback.

What: a long run (an hour of GPU work) tells you where it lives and what it is doing while it runs:
the run id and folder first, then each step's start and end, each GPU lease wait, and the end.

How: turn on ``INFO`` logging for the ``hone_flow`` logger (a command line tool needs nothing else), or
pass ``on_event=`` to ``wf.run`` / ``run.resume`` / ``run.fork``: a callable that receives small dicts
(``run_started``, ``step_started``, ``step_finished``, ``lease_waiting``, ``lease_granted``,
``run_finished``). A callback that raises is logged and ignored.

Why: ``wf.run`` returns only when the call ends, so without this an app has to guess which run folder is
the new one (and two runs started at once break the guess) and poll it from another process. A GPU lease
that never comes shows up as ``lease_waiting`` without ``lease_granted``.
"""

import logging
import tempfile
from typing import Any

import hone_flow as fk
from hone_flow.testing import FakeGpuLease

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

wf = fk.Workflow("progress_demo", storage=tempfile.mkdtemp(), gpu=FakeGpuLease())


@wf.step(resources="gpu:tts", vram_gb=4.0)
def narrate(text: str) -> str:
    return f"(voice) {text}"


@wf.step()
def publish(narrate: str) -> str:
    return narrate.upper()


lines: list[str] = []


def show(event: dict[str, Any]) -> None:
    """A progress line for a terminal or a UI."""
    if event["event"] == "run_started":
        lines.append(f"started {event['run_id']} -> {event['location']}")
    elif event["event"] == "step_finished":
        lines.append(f"{event['step']}/{event['item']} {event['status']} ({event['duration_ms']} ms)")
    elif event["event"] == "run_finished":
        lines.append(f"finished: {event['status']}")


run = wf.run([fk.Item("01", {"text": "hello"}), fk.Item("02", {"text": "world"})], on_event=show)
print("\n".join(lines))

assert lines[0] == f"started {run.run_id} -> {run.location}"
assert lines[-1] == "finished: completed"
assert len(lines) == 6  # start, four steps, end
