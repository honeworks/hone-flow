"""Several processes creating the same fresh span store at the same moment never drop a span."""

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

CREATOR = """
import sys, time, secrets
from pathlib import Path
import hone_flow as fk
from hone_flow.testing.contracts import example_span
folder, start = Path(sys.argv[1]), float(sys.argv[2])
for store in range(4):  # every process creates the same fresh store at the same moment, four times
    time.sleep(max(0.0, start + 0.4 * store - time.time()))
    sink = fk.SqliteSpanSink(folder / f"{store}.db")
    sink.emit({**example_span(), "span_id": secrets.token_hex(8)})
    assert sink.failures == 0, sink.failures
"""


def test_processes_creating_the_same_fresh_store_never_drop_spans(tmp_path: Path) -> None:
    start = time.time() + 1.5  # after every interpreter has started
    procs = [subprocess.Popen([sys.executable, "-c", CREATOR, str(tmp_path), str(start)]) for _ in range(8)]
    assert [p.wait(timeout=60) for p in procs] == [0] * 8
    for store in range(4):
        with sqlite3.connect(tmp_path / f"{store}.db") as conn:
            assert conn.execute("SELECT COUNT(*) FROM spans").fetchone() == (8,)
