"""The ``hone.flow.*`` spans (docs/records.md) and ``SpanLog``, which keeps them in ``<run>/spans.jsonl``.

One ``hone.flow.run`` span per call, one span per (step, item) the call touched (``hone.flow.gate`` for
gates, ``hone.flow.step`` for the others), and one ``hone.flow.gate`` span per review decision.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from hone_flow._records import capture_default, logger, prepare, strip_secrets
from hone_flow._tracing import iso_now, make_span, new_span_id
from hone_flow.dag import Step
from hone_flow.ports import RecordSink, RunStorage
from hone_flow.run_format import Manifest, StepInfo, StepMetadata, digest
from hone_flow.storage import LocalStorage

if TYPE_CHECKING:
    from hone_flow.executor import Call


def json_attribute(value: Any) -> str:
    """A structured attribute value as a JSON string (sorted keys; unknown types via ``str``)."""
    return json.dumps(value, sort_keys=True, default=str)


def run_error(status: str) -> str | None:
    """The status message of a run span: an error when the run failed or was interrupted."""
    return f"run {status}" if status in ("failed", "interrupted") else None


def step_error(traceback: str | None, time: str) -> tuple[str | None, list[dict[str, Any]]]:
    """(status message, events) of a step span: the traceback's last line and an ``exception`` event."""
    if not traceback:
        return None, []
    event = {"name": "exception", "time": time, "attributes": {"exception.stacktrace": traceback}}
    return traceback.strip().splitlines()[-1], [event]


class SpanLog:
    """The spans of one call on a run, written to ``<run>/spans.jsonl`` and to the workflow's ``sink``.

    ``flush`` appends to a local file, or rewrites the whole object on other storage (the run lease
    guarantees a single writer). Secrets are stripped; the content-capture switch is honoured.
    """

    def __init__(self, storage: RunStorage, key: str, sink: RecordSink | None) -> None:
        self.storage, self.key, self.sink = storage, key, sink
        self.pending = b""
        self.sink_failures = 0
        self.written = b"" if isinstance(storage, LocalStorage) else _read_or_empty(storage, key)

    def emit(self, span: Mapping[str, Any]) -> None:
        clean = strip_secrets(dict(span))
        self.pending += (json.dumps(prepare(clean, capture_default()), sort_keys=True) + "\n").encode()
        self._to_sink("emit", clean)

    def flush(self) -> None:
        if isinstance(self.storage, LocalStorage):
            if self.pending:
                self.storage.append(self.key, self.pending)
        else:
            self.written += self.pending
            self.storage.write_bytes(self.key, self.written)
        self.pending = b""
        self._to_sink("flush")

    def _to_sink(self, method: str, *args: Any) -> None:
        """Pass on to the extra sink; a sink never raises into the run."""
        if self.sink is None:
            return
        try:
            getattr(self.sink, method)(*args)
        except Exception:
            self.sink_failures += 1
            if self.sink_failures == 1:
                logger.exception("the sink %r failed; its further failures are only counted", self.sink)


def _read_or_empty(storage: RunStorage, key: str) -> bytes:
    try:
        return storage.read_bytes(key)
    except FileNotFoundError:
        return b""


def _unit_attributes(
    manifest: Manifest, step: Step | StepInfo, item: str | None, status: str
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "hone.step": step.name,
        "hone.flow.workflow": manifest.workflow,
        "hone.flow.workflow_version": manifest.workflow_version,
        "hone.flow.step_version": step.version,
        "hone.flow.source_hash": step.source_hash,
        "hone.flow.status": status,
        "hone.flow.deterministic": step.deterministic,
        "hone.flow.resource": step.resources,
    }
    if item is not None:
        attributes["hone.item"] = item
    return attributes


def _meta_attributes(meta: StepMetadata) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "hone.flow.step_version": meta.version,
        "hone.flow.source_hash": meta.source_hash,
        "hone.flow.attempt": meta.attempt,
        "hone.flow.seed": meta.seed,
        "hone.flow.inputs": json_attribute({n: digest(i.files) for n, i in meta.inputs.items()}),
        "hone.flow.outputs": json_attribute({n: digest(o.files) for n, o in meta.outputs.items() if o.files}),
        "hone.flow.params": json_attribute(meta.params),
        "hone.flow.labels": json_attribute(meta.labels),
    }
    if meta.reused_from:
        attributes["hone.flow.reused_from"] = meta.reused_from
    return attributes


def unit_span(
    call: Call,
    step: Step,
    item: str | None,
    status: str,
    *,
    meta: StepMetadata | None = None,
    extra: Mapping[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The span of one (step, item) the call touched; ``meta`` when the unit ran or was reused;
    ``extra`` attributes and ``events`` (``metrics.sample``) when it was measured."""
    attributes = call.shared | _unit_attributes(call.manifest, step, item, status) | dict(extra or {})
    start = end = iso_now()
    error, error_events = None, []
    if meta is not None:
        attributes |= _meta_attributes(meta)
        start, end = meta.started_at or start, meta.ended_at or end
        error, error_events = step_error(meta.error.traceback if meta.error else None, end)
    return make_span(
        "hone.flow.gate" if step.kind == "gate" else "hone.flow.step",
        attributes,
        trace_id=call.manifest.trace_id,
        span_id=meta.span_id if meta and meta.span_id else new_span_id(),
        parent=call.span_id,
        start=start,
        end=end,
        error=error,
        events=[*(events or []), *error_events],
    )


def state_span(
    manifest: Manifest,
    step: str,
    item: str | None,
    status: str,
    *,
    extra: Mapping[str, Any] | None = None,
    parent: str | None,
) -> dict[str, Any]:
    """The span of a step outside a call: an attempt lost in a crash, or a review decision (``extra``)."""
    info = next(s for s in manifest.steps if s.name == step)
    attributes = (
        {"hone.run_id": manifest.run_id} | _unit_attributes(manifest, info, item, status) | dict(extra or {})
    )
    now = iso_now()
    return make_span(
        "hone.flow.gate" if info.kind == "gate" else "hone.flow.step",
        attributes,
        trace_id=manifest.trace_id,
        span_id=new_span_id(),
        parent=parent,
        start=now,
        end=now,
    )


def run_span(call: Call) -> dict[str, Any]:
    """The ``hone.flow.run`` span of the call (its status is the run status at the end of the call)."""
    manifest = call.manifest
    attributes = call.shared | {
        "hone.flow.workflow": manifest.workflow,
        "hone.flow.workflow_version": manifest.workflow_version,
        "hone.flow.params": json_attribute(manifest.params),
        "hone.flow.status": manifest.status,
        "hone.flow.seed": manifest.seed,
    }
    if manifest.fork_of is not None:
        attributes["hone.flow.fork_of"] = manifest.fork_of.run_id
    if manifest.label is not None:
        attributes["hone.flow.run.label"] = manifest.label
    return make_span(
        "hone.flow.run",
        attributes,
        trace_id=manifest.trace_id,
        span_id=call.span_id,
        parent=call.parent_span_id,
        start=call.started_at,
        end=iso_now(),
        error=run_error(manifest.status),
        events=call.events,
        links=call.links,
    )
