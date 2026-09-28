"""Crash recovery: a process is killed mid-run; another takes the run over and finishes it.

What: this script starts itself as a child process that runs a workflow, kills it with SIGKILL while
step two runs, then resumes the same run in this process. The dead child's ``lease.json`` is taken over,
the step that was running becomes ``interrupted`` and reruns; committed steps are not redone.

How: ``wf.open_run(run_id).resume()``. The run lease (``lease.json``: host, pid, heartbeat) is stale when
its pid is dead on this host (or its heartbeat expired on another host); a live one raises
``fk.RunLocked``. Recovery deletes step folders without ``metadata.json`` (the commit marker).

Why: a crash costs at most the steps that were running. Each step commits on its own: outputs first,
``metadata.json`` last, so a half-written result is never mistaken for a finished one.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import hone_flow as fk

HERE = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="hone-flow-example-"))
wf = fk.Workflow("crash_demo", storage=HERE / "flows")


def log(name: str) -> None:
    with (HERE / "calls.log").open("a") as fh:
        fh.write(name + "\n")


@wf.step()
def fetch(url: str) -> str:
    log("fetch")
    return f"<html>{url}</html>"


@wf.step()
def render(fetch: str, ctx: fk.Context) -> str:
    log("render")
    if (HERE / "slow").exists():  # the child process hangs here until it is killed
        (HERE / "render.started").write_text(ctx.run_id)
        time.sleep(60)
    return fetch.upper()


@wf.step()
def upload(render: str) -> int:
    log("upload")
    return len(render)


if len(sys.argv) > 1 and sys.argv[1] == "child":
    wf.run([fk.Item("01", {"url": "example.org"})])
    sys.exit(0)

(HERE / "slow").write_text("")
child = subprocess.Popen([sys.executable, __file__, "child", str(HERE)])
while not (HERE / "render.started").exists():
    time.sleep(0.05)
os.kill(child.pid, signal.SIGKILL)  # no cleanup, no finally: blocks, like a power cut
child.wait()
run_id = (HERE / "render.started").read_text()
print(
    "killed the child; lease left behind:", Path(HERE, "flows/crash_demo/runs", run_id, "lease.json").exists()
)

(HERE / "slow").unlink()
run = wf.open_run(run_id)
run.resume()  # takes the stale lease over, marks render interrupted, reruns it
statuses = [
    s["attributes"]["hone.flow.status"] for s in run.spans() if s["attributes"].get("hone.step") == "render"
]
print(run.status, "| render spans:", statuses, "| calls:", (HERE / "calls.log").read_text().split())
assert run.status == "completed"
assert statuses == ["interrupted", "done"]
assert (HERE / "calls.log").read_text().split() == ["fetch", "render", "render", "upload"]  # fetch not redone
assert run.output("upload", "01") == len("<HTML>EXAMPLE.ORG</HTML>")
