import json
import sqlite3
from pathlib import Path
from typing import Any

import hone_flow as fk
from hone_flow.testing.contracts import check_record_sink


def test_sqlite_sink_contract(tmp_path: Path) -> None:
    db = tmp_path / "spans.db"

    def read_back() -> list[dict[str, Any]]:
        with sqlite3.connect(db) as conn:
            return [
                {"span_id": s, "trace_id": t} for s, t in conn.execute("SELECT span_id, trace_id FROM spans")
            ]

    check_record_sink(fk.SqliteSpanSink(db), read_back)


def test_jsonl_sink_contract(tmp_path: Path) -> None:
    path = tmp_path / "spans.jsonl"
    check_record_sink(
        fk.JsonlSpanSink(path), lambda: [json.loads(line) for line in path.read_text().splitlines()]
    )


def test_memory_sink_contract() -> None:
    sink = fk.MemorySink()
    check_record_sink(sink, lambda: sink.spans)
