"""Span sinks (the honeworks span format, docs/records.md): SQLite, JSONL, memory and null.

Every sink strips secrets and, with content capture off (``HONE_CAPTURE_CONTENT=0`` or
``capture_content=False``), replaces content attributes with their sha256 and length. Sinks never raise
into the workflow: a failed write is logged once and counted in ``failures``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hone_flow._tracing import SCHEMA_VERSION, iso_now

logger = logging.getLogger("hone_flow")

CONTENT_ATTRIBUTES = ("hone.flow.params", "hone.flow.gate.note")
SECRET = re.compile(r"sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-~+/=]+")
BLOB_LIMIT = 64 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta   (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spans (
  span_id        TEXT PRIMARY KEY,
  trace_id       TEXT NOT NULL,
  parent_span_id TEXT,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'internal',
  start_time     TEXT NOT NULL,
  end_time       TEXT,
  status_code    TEXT NOT NULL DEFAULT 'unset',
  status_message TEXT NOT NULL DEFAULT '',
  attributes     TEXT NOT NULL DEFAULT '{}',
  events         TEXT NOT NULL DEFAULT '[]',
  resource       TEXT NOT NULL DEFAULT '{}',
  links          TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS spans_trace ON spans(trace_id);
CREATE INDEX IF NOT EXISTS spans_name_time ON spans(name, start_time);
CREATE TABLE IF NOT EXISTS blobs (sha256 TEXT PRIMARY KEY, mime TEXT, size INTEGER, data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS changes (seq INTEGER PRIMARY KEY AUTOINCREMENT, span_id TEXT NOT NULL,
                                    op TEXT NOT NULL, at TEXT NOT NULL);
"""


def capture_default() -> bool:
    return os.environ.get("HONE_CAPTURE_CONTENT", "1") != "0"


SECRET_ENV_NAMES: set[str] = set()
"""Environment variables whose values are secrets (webhook URLs of notification destinations)."""


def strip_secrets(value: Any) -> Any:
    """Replace API-key-like strings (``sk-...``, ``Bearer ...``) and the values of ``SECRET_ENV_NAMES``
    with ``***``, recursively."""
    if isinstance(value, str):
        for name in SECRET_ENV_NAMES:
            secret = os.environ.get(name, "")
            if len(secret) >= 8:  # never replace short values everywhere
                value = value.replace(secret, "***")
        return SECRET.sub("***", value)
    if isinstance(value, Mapping):
        return {k: strip_secrets(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, list | tuple):
        return [strip_secrets(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def prepare(span: Mapping[str, Any], capture_content: bool) -> dict[str, Any]:
    """The span as it may be written: secrets stripped, content hashed when capture is off."""
    clean: dict[str, Any] = strip_secrets(dict(span))
    if not capture_content:
        attributes = dict(clean["attributes"])
        for key in CONTENT_ATTRIBUTES:
            if key in attributes:
                text = str(attributes[key]).encode()
                attributes[key] = json.dumps({"sha256": hashlib.sha256(text).hexdigest(), "len": len(text)})
        clean["attributes"] = attributes
    return clean


class _Sink:
    """Shared failure handling: log the first failure, count all of them."""

    def __init__(self, capture_content: bool | None) -> None:
        self.capture_content = capture_default() if capture_content is None else capture_content
        self.failures = 0

    def emit(self, span: Mapping[str, Any]) -> None:
        try:
            self._write(prepare(span, self.capture_content))
        except Exception:
            if self.failures == 0:
                logger.exception("%s could not record a span; further failures are only counted", self)
            self.failures += 1

    def _write(self, span: dict[str, Any]) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        """Spans are written as they are emitted (at span end), so there is nothing to flush."""

    def close(self) -> None:
        """Nothing to release."""


def _enable_wal(db: sqlite3.Connection, attempts: int = 50) -> None:
    """Switch to WAL. The switch ignores busy_timeout when another process is creating the same store at
    the same moment, so retry briefly."""
    for attempt in range(attempts):
        try:
            db.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1)


class SqliteSpanSink(_Sink):
    """Spans in SQLite with the honeworks span schema (WAL, busy timeout 5 s).

    >>> import tempfile, pathlib
    >>> sink = SqliteSpanSink(pathlib.Path(tempfile.mkdtemp()) / "spans.db")
    """

    def __init__(self, path: str | os.PathLike[str], *, capture_content: bool | None = None) -> None:
        super().__init__(capture_content)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False)
        self._db.execute("PRAGMA busy_timeout=5000")
        _enable_wal(self._db)
        rows = [("schema", "hone-spans"), ("schema_version", SCHEMA_VERSION), ("package", "hone-flow")]
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            self._db.executemany(
                "INSERT OR IGNORE INTO meta VALUES (?, ?)", [*rows, ("created_at", iso_now())]
            )

    def __repr__(self) -> str:
        return f"SqliteSpanSink({str(self.path)!r})"

    def _blob(self, value: Any) -> Any:
        """Store a large string attribute in ``blobs`` and return its reference."""
        if not isinstance(value, str):
            return value
        data = value.encode()
        if len(data) <= BLOB_LIMIT:
            return value
        digest = hashlib.sha256(data).hexdigest()
        self._db.execute(
            "INSERT OR IGNORE INTO blobs VALUES (?, ?, ?, ?)", (digest, "text/plain", len(data), data)
        )
        return {"$blob": digest}

    def _write(self, span: dict[str, Any]) -> None:
        with self._lock, self._db:
            attributes = {k: self._blob(v) for k, v in span["attributes"].items()}
            self._db.execute(
                "INSERT OR REPLACE INTO spans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    span["span_id"],
                    span["trace_id"],
                    span["parent_span_id"],
                    span["name"],
                    span["kind"],
                    span["start_time"],
                    span["end_time"],
                    span["status"]["code"],
                    span["status"]["message"],
                    json.dumps(attributes),
                    json.dumps(span["events"]),
                    json.dumps(span["resource"]),
                    json.dumps(span["links"]),
                ),
            )
            self._db.execute(
                "INSERT INTO changes (span_id, op, at) VALUES (?, 'insert', ?)", (span["span_id"], iso_now())
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()


class JsonlSpanSink(_Sink):
    """One span per line of a JSON Lines file."""

    def __init__(self, path: str | os.PathLike[str], *, capture_content: bool | None = None) -> None:
        super().__init__(capture_content)
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"JsonlSpanSink({str(self.path)!r})"

    def _write(self, span: dict[str, Any]) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(span) + "\n")


class MemorySink(_Sink):
    """Keeps spans in ``self.spans`` (for tests)."""

    def __init__(self, *, capture_content: bool | None = None) -> None:
        super().__init__(capture_content)
        self.spans: list[dict[str, Any]] = []

    def _write(self, span: dict[str, Any]) -> None:
        self.spans.append(span)


class NullSink:
    """Drops every span."""

    def emit(self, span: Mapping[str, Any]) -> None:
        """Ignore the span."""

    def flush(self) -> None:
        """Nothing to flush."""

    def close(self) -> None:
        """Nothing to close."""
