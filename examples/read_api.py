"""The read API: list and inspect runs without the workflow's code.

What: ``fk.open_runs(storage, name)`` gives a ``RunHistory`` for any tool that only knows where runs
are stored (hone-lens, a dashboard, a script). ``runs(updated_since=...)`` lists ``RunSummary`` rows,
newest first; ``open_run(run_id)`` gives a *detached* ``Run``: ``manifest``, ``steps()`` (``StepRecord``:
status, attempts, reviews, reuse, errors, timings), ``output()``, ``spans()``, reviews, ``pin()`` and
``retry_notifications()``. ``resume()`` and ``fork()`` need the workflow and raise.

How: point ``fk.open_runs`` at the same storage and workflow name the runs were made with. Use
``updated_since`` to pick up only runs that changed since your last visit. A table of thousands of runs
lists ``run_ids()`` (no manifest read) and reads one page with ``summaries(ids)``; ``run.lease()`` says
whether a process still holds a run; ``hone_flow.storage.list_dir`` / ``read_range`` browse its files.

Why: run folders are the source of truth and their format is public (``docs/run-format.md``), so reading
them never requires importing the code that made them, or a database.
"""

import tempfile
import time

import hone_flow as fk

storage = tempfile.mkdtemp()


def producer() -> None:
    """Somewhere else, some other day: the code that makes the runs."""
    wf = fk.Workflow("reports", storage=storage)

    @wf.step()
    def summarize(text: str) -> dict[str, int]:
        if not text:
            raise ValueError("empty document")
        return {"words": len(text.split())}

    @wf.gate()
    def check(summarize: dict[str, int]) -> dict[str, int]:
        return summarize

    wf.run([fk.Item("doc-1", {"text": "a b c"}), fk.Item("doc-2", {"text": ""})])


producer()
time.sleep(0.01)
since = fk.open_runs(storage, "reports").runs()[0].updated_at
producer()

history = fk.open_runs(storage, "reports")  # no workflow code from here on
for summary in history.runs():
    print(summary.run_id, summary.status, summary.items, "pinned" if summary.pinned else "")
newest = history.runs(updated_since=since)[0]
run = history.open_run(newest.run_id)
for record in run.steps():
    print(f"  {record.step}/{record.item}: {record.status}", record.error["message"] if record.error else "")
print("  output:", run.output("summarize", "doc-1"))

run.approve(step="check", item="doc-1", actor="dashboard")  # detached runs can take reviews
assert run.steps("check", "doc-1")[0].reviews[0]["actor"] == "dashboard"
assert [r.status for r in run.steps("summarize")] == ["done", "failed"]
assert run.steps("check", "doc-2")[0].status == "blocked"
try:
    run.resume()
except fk.HoneFlowError as exc:
    print("detached resume:", exc)
else:
    raise AssertionError("a detached run cannot resume")

ids = history.run_ids()  # one-level listings only: cheap however many files the runs hold
page = history.summaries(ids[:10])  # reads only these manifests
print("page:", [(s.run_id, s.status, s.attempts) for s in page])
assert {s.run_id for s in page} == {s.run_id for s in history.runs()}  # ids order to the second
assert run.lease() is None  # no process holds it now
