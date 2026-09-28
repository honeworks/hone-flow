"""Trace context (design §4.12), span ids and the span dict (the span format of docs/records.md)."""

from __future__ import annotations

import os
import secrets
import socket
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = "1"
_current: ContextVar[dict[str, str] | None] = ContextVar("hone_flow_trace", default=None)


def iso_time(moment: datetime) -> str:
    """A UTC time as ISO-8601 with milliseconds, e.g. ``2026-09-27T14:03:11.120Z``."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_now() -> str:
    """The current UTC time as ISO-8601 with milliseconds."""
    return iso_time(datetime.now(UTC))


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"


def current_trace() -> dict[str, str]:
    """The trace context of the step running now (empty outside a step).

    Pass it on to other packages: ``client.complete(..., trace=hone_flow.current_trace())``.
    """
    return dict(_current.get() or {})


def incoming_trace(trace: Mapping[str, str] | None) -> dict[str, str]:
    """The caller's trace context: ``trace=`` when given, else the one active now (maybe empty)."""
    return dict(trace) if trace is not None else current_trace()


@contextmanager
def using_trace(trace: Mapping[str, str]) -> Generator[None]:
    """Make ``trace`` the current context inside the block."""
    token = _current.set(dict(trace))
    try:
        yield
    finally:
        _current.reset(token)


def parent_of(trace: Mapping[str, str]) -> tuple[str, str | None]:
    """(trace id, parent span id) for a new span: from the context's ``traceparent``, or a new trace."""
    parts = trace.get("traceparent", "").split("-")
    if len(parts) == 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
        return parts[1], parts[2]
    return new_trace_id(), None


def make_span(
    name: str,
    attributes: Mapping[str, Any],
    *,
    trace_id: str,
    span_id: str,
    parent: str | None,
    start: str,
    end: str,
    error: str | None = None,
    events: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A span dict (kind ``internal``); ``error`` sets status ``error`` with that message."""
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent,
        "name": name,
        "kind": "internal",
        "start_time": start,
        "end_time": end,
        "status": {"code": "error", "message": error} if error else {"code": "ok", "message": ""},
        "attributes": {"hone.schema_version": SCHEMA_VERSION, **attributes},
        "events": events or [],
        "resource": _resource(),
        "links": links or [],
    }


def _resource() -> dict[str, Any]:
    from hone_flow import __version__  # noqa: PLC0415 - the package imports this module first

    return {
        "service.name": os.environ.get("OTEL_SERVICE_NAME", "hone-flow"),
        "hone.package": "hone-flow",
        "hone.package.version": __version__,
        "host.name": socket.gethostname(),
        "process.pid": os.getpid(),
    }
