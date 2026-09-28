"""Progress while a run runs (design §4.17, change 0003): INFO log lines on the ``hone_flow`` logger and
the same events, as small dicts, to an optional ``on_event`` callback of ``run`` / ``resume`` / ``fork``.

Events: ``run_started`` (``kind``, ``location``), ``step_started`` (``step``, ``item``, ``attempt``),
``step_finished`` (also ``status``, ``duration_ms``), ``lease_waiting`` (``name``, ``vram_gb``),
``lease_granted`` (``name``, ``wait_ms``) and ``run_finished`` (``status``). Every event also has
``event``, ``run_id``, ``workflow`` and ``at``. A callback that raises is logged and ignored: progress
never changes a run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hone_flow._tracing import iso_now

if TYPE_CHECKING:
    from hone_flow.executor import Call

logger = logging.getLogger("hone_flow")

OnEvent = Callable[[dict[str, Any]], None]

MESSAGES = {
    "run_started": "run {run_id} of {workflow} ({kind}) at {location}",
    "step_started": "step {unit} started (attempt {attempt})",
    "step_finished": "step {unit} {status} in {seconds:.1f} s",
    "lease_waiting": "waiting for GPU lease {name!r} ({vram_gb} GB)",
    "lease_granted": "GPU lease {name!r} granted after {seconds:.1f} s",
    "run_finished": "run {run_id} of {workflow} {status}",
}


def report(call: Call, event: str, **fields: Any) -> None:
    """Log one progress event and pass it to the call's ``on_event`` callback (if any)."""
    manifest = call.manifest
    data = {"event": event, "run_id": manifest.run_id, "workflow": manifest.workflow, "at": iso_now()}
    data |= fields
    if logger.isEnabledFor(logging.INFO):
        logger.info(MESSAGES[event].format(**data, **_display(data)))
    if call.on_event is None:
        return
    try:
        call.on_event(dict(data))
    except Exception:  # a progress display must never break the run
        logger.warning("the on_event callback failed on %s; ignoring it", event, exc_info=True)


def _display(data: dict[str, Any]) -> dict[str, Any]:
    """Extra names the log messages use: ``step/item`` and seconds."""
    item = data.get("item")
    unit = f"{data.get('step')}/{item}" if item is not None else str(data.get("step"))
    millis = data.get("duration_ms", data.get("wait_ms", 0)) or 0
    return {"unit": unit, "seconds": millis / 1000}
