"""One call on a run (``wf.run``, ``run.resume``, a fork): walk the steps in order, batch GPU leases,
isolate failures, and finish the run (status, spans).

The caller holds the run lease. Each (step, item) is a *unit*; its state lives in the manifest's
``state`` table (a summary) and its ``metadata.json`` (the truth, written by ``commit.run_attempt``).
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import tempfile
import time
from collections.abc import Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hone_flow._tracing import incoming_trace, iso_now, new_span_id, parent_of
from hone_flow.commit import run_attempt
from hone_flow.dag import FAN_IN, Step
from hone_flow.errors import HoneFlowError, StepFailed
from hone_flow.notifications import add_event, deliver_event
from hone_flow.progress import OnEvent, report
from hone_flow.reports import write_reports
from hone_flow.run_format import CallInfo, Manifest, RunFolder, split_state_key, state_key
from hone_flow.schedule import decide, phases, units
from hone_flow.spans import SpanLog, run_span, unit_span

if TYPE_CHECKING:
    from hone_flow.workflow import Workflow

logger = logging.getLogger("hone_flow")


@dataclass
class Call:
    """What a call needs while it walks the steps."""

    wf: Workflow
    folder: RunFolder
    manifest: Manifest
    kind: str  # run, resume, fork
    params: dict[str, Any]  # the values steps receive
    steps: set[str]  # selected by `until`
    until: str | None
    items: list[str] | None  # selected items; None: all
    workdir: Path
    spans: SpanLog
    span_id: str  # this call's hone.flow.run span
    parent_span_id: str | None
    shared: dict[str, str]  # trace keys copied onto every span and into every step's context
    started_at: str = field(default_factory=iso_now)
    lease_wait_ms: float = 0.0  # time spent waiting for the GPU lease, charged to the next attempt
    events: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])  # run span events
    links: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])  # run span links (fork)
    touched: set[str] = field(default_factory=set[str])  # units seen; depth-first meets global steps per item
    on_event: OnEvent | None = None  # progress callback (design §4.17)


def open_call(
    wf: Workflow,
    folder: RunFolder,
    *,
    kind: str,
    params: Mapping[str, Any],
    until: str | None = None,
    items: Iterable[str] | None = None,
    trace: Mapping[str, str] | None = None,
    on_event: OnEvent | None = None,
) -> Call:
    """Prepare a call on a run whose lease this process holds (nothing is written yet)."""
    manifest = folder.read_manifest()
    incoming = incoming_trace(trace)
    trace_id, parent = parent_of(incoming)
    graph = wf.graph()
    return Call(
        wf=wf,
        folder=folder,
        manifest=manifest,
        kind=kind,
        params=dict(params),
        steps=graph.upstream([until]) if until else set(graph.steps),
        until=until,
        items=sorted(items) if items is not None else None,
        workdir=Path(tempfile.mkdtemp(prefix="hone-flow-", dir=os.environ.get("HONE_FLOW_WORKDIR"))),
        spans=SpanLog(folder.storage, folder.key("spans.jsonl"), wf.sink),
        span_id=new_span_id(),
        parent_span_id=parent if trace_id == manifest.trace_id else None,
        shared={k: v for k, v in incoming.items() if k != "traceparent"} | {"hone.run_id": manifest.run_id},
        on_event=on_event,
    )


def execute(call: Call) -> None:
    """Mark the run running, walk its steps, and finish (also when a step raises ``StepFailed``)."""
    call.manifest.status = "running"
    call.manifest.calls.append(
        CallInfo(
            kind=call.kind,
            started_at=call.started_at,
            host=socket.gethostname(),
            pid=os.getpid(),
            until=call.until,
            items=call.items,
            span_id=call.span_id,
        )
    )
    call.folder.write_manifest(call.manifest)
    report(call, "run_started", kind=call.kind, location=call.folder.location)
    try:
        walk(call)
    finally:
        finish(call)
        shutil.rmtree(call.workdir, ignore_errors=True)


def walk(call: Call) -> None:
    """Breadth-first: each step for all items, then the next. Depth-first: each item through the steps
    up to the next fan-in step, then that step (design §4.18), and so on."""
    graph = call.wf.graph()
    if call.wf.order != "depth_first":
        _walk_steps(call, list(graph.steps.values()), None)
        return
    for phase in phases(graph):
        if phase[0].kind in FAN_IN:
            _walk_steps(call, phase, None)
            continue
        _walk_steps(call, [s for s in phase if s.once], None)  # global steps (they may produce items)
        visited: set[str] = set()
        while new := [i.id for i in call.manifest.items if i.id not in visited]:  # items may be added
            for item_id in new:
                visited.add(item_id)
                _walk_steps(call, phase, item_id)


def _walk_steps(call: Call, steps: list[Step], only: str | None) -> None:
    """Visit the units of ``steps`` (of item ``only``, or all). Neighbouring steps with the same ``gpu:``
    tag share one lease, taken before their first call."""
    for tag, group in groupby(steps, key=lambda s: s.resources):
        batch_steps = list(group)
        with ExitStack() as batch:
            due = (tag, max(s.vram_gb for s in batch_steps)) if tag.startswith("gpu:") else None
            for step in batch_steps:
                for item in units(call, step, only):
                    key = state_key(step.name, item)
                    if key in call.touched:
                        continue
                    call.touched.add(key)
                    decision = decide(call, step, item)
                    if decision == "run" and due is not None:
                        _take_gpu_lease(call, batch, *due)
                        due = None
                    _apply(call, step, item, decision)


def _take_gpu_lease(call: Call, batch: ExitStack, tag: str, vram_gb: float) -> None:
    started = time.monotonic()
    trace = call.shared | {"traceparent": f"00-{call.manifest.trace_id}-{call.span_id}-01"}
    report(call, "lease_waiting", name=tag, vram_gb=vram_gb)
    try:
        batch.enter_context(call.wf.gpu.lease(tag, vram_gb, trace=trace))
    except Exception as exc:
        raise HoneFlowError(f"could not take the GPU lease {tag!r} ({vram_gb} GB): {exc}") from exc
    call.lease_wait_ms = (time.monotonic() - started) * 1000
    report(call, "lease_granted", name=tag, wait_ms=round(call.lease_wait_ms))


def _apply(call: Call, step: Step, item: str | None, decision: str) -> None:
    if decision == "keep":
        return
    if decision != "run":
        call.manifest.state[state_key(step.name, item)] = decision
        call.spans.emit(unit_span(call, step, item, decision))
        return
    meta = run_attempt(call, step, item)
    if meta.error is not None and call.wf.fail_fast:
        raise StepFailed(step.name, item, meta.error.traceback, run_id=call.manifest.run_id)


def run_status(states: Iterable[str]) -> str:
    """The run status after a call; the first match wins."""
    found = set(states)
    if "failed" in found:
        return "failed"
    if "awaiting_review" in found:
        return "awaiting_review"
    if found & {"pending", "skipped", "blocked", "interrupted", "running"}:
        return "partial"
    return "completed"


def finish(call: Call) -> None:
    """Record the call's outcome: run status, the run span, spans.jsonl, the manifest, then reports."""
    manifest = call.manifest
    interrupted = [k for k, s in manifest.state.items() if s == "running"]  # the call raised mid-step
    for key in interrupted:
        manifest.state[key] = "interrupted"
        step, item = split_state_key(key)
        call.spans.emit(unit_span(call, call.wf.graph().steps[step], item, "interrupted"))
    manifest.status = "interrupted" if interrupted else run_status(manifest.state.values())
    manifest.calls[-1].ended_at = iso_now()
    call.spans.emit(run_span(call))
    call.spans.flush()
    event = add_event(call.folder, manifest, call.wf.notifications)
    call.folder.write_manifest(manifest)  # the status and a pending event, before any delivery
    try:
        write_reports(call.folder, manifest)
    except Exception:  # reports are for people; failing to write them must not fail the run
        logger.warning("could not write the reports of run %s", manifest.run_id, exc_info=True)
    if event is not None:
        try:
            deliver_event(call.folder, manifest, event)
        except Exception:  # deliveries are retried on request; they never fail the run
            logger.warning("could not record the notifications of run %s", manifest.run_id, exc_info=True)
    report(call, "run_finished", status=manifest.status)
