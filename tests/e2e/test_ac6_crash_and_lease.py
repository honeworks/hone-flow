"""AC-6: a SIGKILLed run is taken over and resumed; the run lease keeps a second process out."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e

FLOW = """
import os, time
from pathlib import Path
import hone_flow as fk

HERE = Path(os.environ["CRASH_DIR"])
wf = fk.Workflow("crashy", storage=HERE / "flows")

def log(name):
    with open(HERE / "calls.log", "a") as fh:
        fh.write(name + "\\n")

@wf.step()
def first(text: str) -> str:
    log("first")
    return text.upper()

@wf.step()
def second(first: str, ctx: fk.Context) -> str:
    log("second")
    if (HERE / "slow").exists():
        (HERE / "second.started").write_text(ctx.run_id)
        time.sleep(60)
    return first + "!"

@wf.step()
def third(second: str) -> str:
    log("third")
    return second + "?"

if __name__ == "__main__":
    wf.run([fk.Item("01", {"text": "hi"})])
"""


def load_flow(folder: Path, monkeypatch: pytest.MonkeyPatch) -> fk.Workflow:
    monkeypatch.setenv("CRASH_DIR", str(folder))
    monkeypatch.syspath_prepend(str(folder))
    sys.modules.pop("crashflow", None)
    import crashflow  # type: ignore[import-not-found]

    return crashflow.wf  # type: ignore[no-any-return]


def test_ac6_crash_and_lease_sigkill_then_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "crashflow.py").write_text(FLOW)
    (tmp_path / "slow").write_text("")
    env = os.environ | {"CRASH_DIR": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, str(tmp_path / "crashflow.py")], env=env)
    deadline = time.monotonic() + 30
    while not (tmp_path / "second.started").exists():
        assert time.monotonic() < deadline, "step two never started"
        time.sleep(0.05)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait()
    run_id = (tmp_path / "second.started").read_text()
    folder = tmp_path / "flows" / "crashy" / "runs" / run_id
    assert (folder / "lease.json").exists()  # left behind by the killed process
    assert json.loads((folder / "manifest.json").read_text())["state"]["second/01"] == "running"
    assert not (folder / "second/item_01/metadata.json").exists()  # never committed
    (folder / "second/item_01/output").mkdir(parents=True, exist_ok=True)
    (folder / "second/item_01/output/half.bin").write_bytes(b"partial upload")  # as if cut mid-write

    (tmp_path / "slow").unlink()
    wf = load_flow(tmp_path, monkeypatch)
    run = wf.open_run(run_id)
    assert run.status == "running"  # nobody has taken it over yet
    run.resume()
    assert run.status == "completed"
    assert run.output("third", "01") == "HI!?"
    assert (tmp_path / "calls.log").read_text().split() == ["first", "second", "second", "third"]
    assert not (folder / "lease.json").exists()
    assert not (folder / "second/item_01/output/half.bin").exists()  # the uncommitted folder was removed
    second = run.steps("second", "01")[0]
    assert (second.attempt, second.attempts) == (1, [])  # an interrupted attempt was never committed
    statuses = [
        s["attributes"]["hone.flow.status"]
        for s in run.spans()
        if s["attributes"].get("hone.step") == "second"
    ]
    assert statuses == ["interrupted", "done"]
    runs = [s for s in run.spans() if s["name"] == "hone.flow.run"]
    assert [s["attributes"]["hone.flow.status"] for s in runs] == ["completed"]  # the killed call never ended
    for output in folder.rglob("output"):
        meta = json.loads((output.parent / "metadata.json").read_text())
        stored = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
        assert stored == {f for o in meta["outputs"].values() for f in o["files"]}  # no partial files


def plant_lease(folder: Path, *, host: str, pid: int, expires_in: float) -> None:
    now = datetime.now(UTC)
    stamp = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    expires = (now + timedelta(seconds=expires_in)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    lease = {
        "owner": "someone-else",
        "host": host,
        "pid": pid,
        "acquired_at": stamp,
        "heartbeat_at": stamp,
        "expires_at": expires,
    }
    (folder / "lease.json").write_text(json.dumps(lease))


def failing_run(tmp_path: Path) -> tuple[fk.Workflow, fk.Run, Path]:
    wf = fk.Workflow("leases", storage=tmp_path)
    state = {"fail": True}

    @wf.step()
    def flaky(text: str) -> str:
        if state["fail"]:
            raise RuntimeError("try again")
        return text

    run = wf.run([fk.Item("01", {"text": "x"})])
    state["fail"] = False
    return wf, run, Path(run.location)


def test_ac6_crash_and_lease_live_lease_blocks(tmp_path: Path) -> None:
    _, run, folder = failing_run(tmp_path)
    plant_lease(folder, host=socket.gethostname(), pid=os.getpid(), expires_in=-5)  # this process: alive
    with pytest.raises(fk.RunLocked, match=rf"pid {os.getpid()} on {socket.gethostname()}"):
        run.resume()
    plant_lease(folder, host="render-box-2", pid=1, expires_in=60)  # another host, heartbeat fresh
    with pytest.raises(fk.RunLocked, match="render-box-2"):
        run.resume()
    assert run.status == "failed"  # nothing ran


def test_ac6_crash_and_lease_stale_leases_are_taken_over(tmp_path: Path) -> None:
    _, run, folder = failing_run(tmp_path)
    plant_lease(folder, host="render-box-2", pid=1, expires_in=-1)  # another host, expired
    run.resume()
    assert run.status == "completed"
    dead = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True
    )
    plant_lease(folder, host=socket.gethostname(), pid=int(dead.stdout), expires_in=60)  # dead pid here
    run.resume()
    assert not (folder / "lease.json").exists()


def test_ac6_crash_and_lease_heartbeat_refreshes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hone_flow import run_lease

    monkeypatch.setattr(run_lease, "HEARTBEAT_S", 0.05)
    wf = fk.Workflow("beat", storage=tmp_path)
    seen: list[dict[str, str]] = []

    @wf.step()
    def wait(ctx: fk.Context) -> int:
        lease = Path(tmp_path, "beat", "runs", ctx.run_id, "lease.json")
        seen.append(json.loads(lease.read_text()))
        time.sleep(0.3)
        seen.append(json.loads(lease.read_text()))
        return 1

    wf.run([fk.Item("01")])
    assert seen[0]["owner"] == seen[1]["owner"]
    assert seen[1]["heartbeat_at"] > seen[0]["heartbeat_at"]
    assert seen[1]["expires_at"] > seen[1]["heartbeat_at"]


def test_ac6_crash_and_lease_committed_step_is_not_rerun(tmp_path: Path) -> None:
    """A crash after metadata.json was written but before the manifest caught up: metadata wins."""
    calls: list[str] = []
    wf = fk.Workflow("window", storage=tmp_path)

    @wf.step()
    def a(text: str) -> str:
        calls.append("a")
        return text

    @wf.step()
    def b(a: str) -> str:
        calls.append("b")
        return a + "!"

    run = wf.run([fk.Item("01", {"text": "x"})])
    folder = Path(run.location)
    manifest = json.loads((folder / "manifest.json").read_text())
    manifest["state"]["b/01"] = "running"  # as if the process died right after committing b
    manifest["status"] = "running"
    (folder / "manifest.json").chmod(0o644)
    (folder / "manifest.json").write_text(json.dumps(manifest))
    plant_lease(folder, host="elsewhere", pid=1, expires_in=-1)
    calls.clear()
    run.resume()
    assert calls == []
    assert run.status == "completed"
    assert run.steps("b", "01")[0].attempt == 1
    assert "interrupted" not in [s["attributes"]["hone.flow.status"] for s in run.spans()]
