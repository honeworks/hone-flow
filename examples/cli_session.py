"""The CLI: run, review, resume and fork from a shell (extra ``cli``).

What: the ``hone-flow`` command does what the library does, for scripts and people:
``run``, ``resume``, ``fork`` (``--dry-run``), ``status``, ``show``, ``runs``, ``approve``, ``edit``
(``$EDITOR``), ``reject``, ``pin``, ``cleanup`` and ``notify-retry``, each with ``--json``.

How: ``--flow module:attribute`` imports your workflow (from the current folder); read and review
commands also work with ``--storage URL --name NAME`` and no code. Items come from a JSON file
(``[{"id": ..., "inputs": {...}}]``; files as ``{"$file": "path"}``). Exit codes: 0 success (also a run
waiting at a gate), 1 failure (``error: ...``), 2 usage error. This example drives it with
``subprocess`` exactly as a shell would.

Why: a reviewer can approve gates and a cron job can resume, pin or clean up runs without writing
Python.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

work = Path(tempfile.mkdtemp(prefix="hone-flow-example-"))
(work / "myflows.py").write_text("""
import hone_flow as fk

wf = fk.Workflow("cli_demo", storage="flows")

@wf.step()
def draft(topic: str, tone: fk.Param[str]) -> str:
    return f"a {tone} note about {topic}"

@wf.gate()
def review(draft: str) -> str:
    return draft

@wf.step()
def send(review: str) -> str:
    return review.upper()
""")
(work / "items.json").write_text(json.dumps([{"id": "01", "inputs": {"topic": "tides"}}]))


def hone_flow(*args: str) -> Any:
    """Run ``hone-flow <args> --json`` in the work folder and parse its output."""
    command = [sys.executable, "-m", "hone_flow.cli", *args, "--json"]  # same as the `hone-flow` script
    result = subprocess.run(command, cwd=work, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


started = hone_flow("run", "--flow", "myflows:wf", "--items", "items.json", "--param", "tone=calm")
run_id = str(started["run_id"])
print("run:", run_id, started["status"])
print(
    "approve:",
    hone_flow(
        "approve", run_id, "--step", "review", "--item", "01", "--storage", "flows", "--name", "cli_demo"
    ),
)  # no code needed to review
print("resume:", hone_flow("resume", run_id, "--flow", "myflows:wf")["status"])
plan = hone_flow("fork", run_id, "--flow", "myflows:wf", "--param", "tone=urgent", "--dry-run")
for row in plan["rows"]:
    print("plan:", row["action"], row["step"], row["reason"])
runs = hone_flow("runs", "--storage", "flows", "--name", "cli_demo")

assert started["status"] == "awaiting_review"
assert plan["rows"][0]["reason"] == "param_changed:tone"
assert runs[0]["status"] == "completed"
failed = subprocess.run(
    [sys.executable, "-m", "hone_flow.cli", "status", "nope", "--flow", "myflows:wf"],
    cwd=work,
    capture_output=True,
    text=True,
)
print("exit code", failed.returncode, failed.stderr.strip())
assert failed.returncode == 1 and failed.stderr.startswith("error: ")
