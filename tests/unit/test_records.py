import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk
from hone_flow._tracing import current_trace, make_span, parent_of, traceparent, using_trace
from hone_flow.testing.contracts import example_span

FAKE_KEY = "sk-test1234567890abcdefXYZ"


def span_with(**attributes: Any) -> dict[str, Any]:
    return example_span() | {"attributes": {"hone.schema_version": "1", **attributes}}


def rows(db: Path, sql: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def test_sqlite_sink_schema_and_round_trip(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    sink = fk.SqliteSpanSink(db)
    sink.emit(example_span())
    sink.close()
    tables = {r[0] for r in rows(db, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"meta", "spans", "blobs", "changes"} <= tables
    meta = dict(rows(db, "SELECT key, value FROM meta"))
    assert meta["schema"] == "hone-spans"
    assert meta["schema_version"] == "1"
    assert meta["package"] == "hone-flow"
    assert rows(db, "PRAGMA journal_mode") == [("wal",)]
    (span,) = rows(db, "SELECT span_id, trace_id, parent_span_id, name, status_code, attributes FROM spans")
    assert span[:5] == (
        "00f067aa0ba902b7",
        "4bf92f3577b34da6a3ce929d0e0e4736",
        None,
        "hone.test.example",
        "ok",
    )
    assert json.loads(span[5]) == {"hone.schema_version": "1"}
    assert rows(db, "SELECT span_id, op FROM changes") == [("00f067aa0ba902b7", "insert")]


def test_large_attributes_go_to_blobs(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    big = "x" * (64 * 1024 + 1)
    fk.SqliteSpanSink(db).emit(span_with(**{"hone.flow.params": big}))
    (attributes,) = rows(db, "SELECT attributes FROM spans")[0]
    ref = json.loads(attributes)["hone.flow.params"]
    (data,) = rows(db, f"SELECT data FROM blobs WHERE sha256 = '{ref['$blob']}'")[0]  # noqa: S608
    assert data.decode() == big


def test_secrets_are_never_written(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"
    params = json.dumps({"api_key": FAKE_KEY, "auth": "Bearer abc.def-ghi"})
    fk.SqliteSpanSink(db).emit(span_with(**{"hone.flow.params": params}))
    jsonl = tmp_path / "spans.jsonl"
    fk.JsonlSpanSink(jsonl).emit(span_with(**{"hone.flow.params": params}))
    assert FAKE_KEY.encode() not in db.read_bytes()
    assert b"abc.def-ghi" not in db.read_bytes()
    assert FAKE_KEY not in jsonl.read_text()
    assert '\\"api_key\\": \\"***\\"' in jsonl.read_text()


def test_capture_off_keeps_only_hash_and_length(monkeypatch: pytest.MonkeyPatch) -> None:
    sink = fk.MemorySink(capture_content=False)
    sink.emit(span_with(**{"hone.flow.params": '{"style": "noir"}', "hone.flow.gate.note": "darker"}))
    attributes = sink.spans[0]["attributes"]
    digest = hashlib.sha256(b"darker").hexdigest()
    assert json.loads(attributes["hone.flow.gate.note"]) == {"sha256": digest, "len": 6}
    assert "noir" not in json.dumps(attributes)
    monkeypatch.setenv("HONE_CAPTURE_CONTENT", "0")
    assert fk.MemorySink().capture_content is False


def test_sink_failures_are_logged_once_and_counted(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    sink = fk.SqliteSpanSink(tmp_path / "spans.db")
    sink.close()
    with caplog.at_level(logging.ERROR, logger="hone_flow"):
        sink.emit(example_span())
        sink.emit(example_span())
    assert sink.failures == 2
    assert caplog.text.count("could not record a span") == 1


def test_null_sink_drops_everything() -> None:
    sink = fk.NullSink()
    sink.emit(example_span())
    sink.flush()
    sink.close()


def test_trace_context_helpers() -> None:
    assert current_trace() == {}
    parent = traceparent("a" * 32, "b" * 16)
    assert parent_of({"traceparent": parent}) == ("a" * 32, "b" * 16)
    trace_id, parent_id = parent_of({"traceparent": "garbage"})
    assert (len(trace_id), parent_id) == (32, None)
    with using_trace({"traceparent": parent, "hone.item": "1"}):
        assert fk.current_trace()["hone.item"] == "1"
        assert parent_of(current_trace()) == ("a" * 32, "b" * 16)
    assert current_trace() == {}
    span = make_span(
        "x", {}, trace_id="a" * 32, span_id="c" * 16, parent=None, start="t0", end="t1", error="boom"
    )
    assert span["status"] == {"code": "error", "message": "boom"}
    assert span["resource"]["hone.package"] == "hone-flow"
