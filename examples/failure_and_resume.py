"""Failures and resume: one item fails, the others finish, a fix continues the same run.

What: a step raises for one item. That (step, item) is ``failed`` with its traceback, the item's
downstream steps are ``blocked``, other items complete, and the run ends ``failed``. After the fix,
``run.resume()`` reruns only the failed and blocked work, recording a second attempt.

How: run; read ``run.steps()`` (status, ``error``, ``attempts``); fix the cause; ``run.resume()`` (or
``wf.open_run(run_id).resume()`` from another process). The failed attempt is kept in ``attempts`` and
whatever it wrote in its work folder under ``attempts/1/output/``.

Why: slow AI steps should never be redone because an unrelated item failed. Resume continues the same
run id, keeps the same seeds, and never mixes step versions (see ``incompatible_resume.py``).
``Workflow(fail_fast=True)`` stops at the first failure instead (``fk.StepFailed``).
"""

import tempfile
from pathlib import Path

import hone_flow as fk

calls: list[str] = []
flaky = {"02": True}  # the "bug": item 02 fails until we fix it
wf = fk.Workflow("resume_demo", storage=tempfile.mkdtemp())


@wf.step()
def draft(text: str, ctx: fk.Context) -> str:
    calls.append(f"draft/{ctx.item_id}")
    ctx.new_file("notes.txt").write_text(f"attempt {ctx.attempt}")
    if flaky.get(ctx.item_id):
        raise ValueError(f"cannot draft {text!r}")
    return text.upper()


@wf.step()
def publish(draft: str, ctx: fk.Context) -> str:
    calls.append(f"publish/{ctx.item_id}")
    return draft + "!"


run = wf.run([fk.Item("01", {"text": "ok"}), fk.Item("02", {"text": "tricky"})])
print(run.status, {f"{r.step}/{r.item}": r.status for r in run.steps()})
failed = run.steps("draft", "02")[0]
print("error:", failed.error["message"] if failed.error else None)
assert run.status == "failed"
assert run.steps("publish", "02")[0].status == "blocked"
assert run.output("publish", "01") == "OK!"  # the other item finished

flaky["02"] = False  # fix the cause (same step version)
calls.clear()
run.resume()
print(run.status, calls)
record = run.steps("draft", "02")[0]
print("attempt", record.attempt, "earlier:", [(a["attempt"], a["status"]) for a in record.attempts])
assert calls == ["draft/02", "publish/02"]  # only the failed and blocked work
assert run.status == "completed"
assert record.attempt == 2
assert Path(run.location, "draft/item_02/attempts/1/output/notes.txt").read_text() == "attempt 1"
