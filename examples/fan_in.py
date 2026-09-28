"""Final steps: one step over every item's result (fan-in).

What: a ``@wf.final_step`` runs once after the items. A parameter named after an item step receives a
``dict`` from item id to that step's output, so one step can build a workbook from every lesson.

How: decorate the step with ``@wf.final_step()`` and name the item step it combines. It lives at
``<step>/`` like a global step, snapshots every item's input under ``inputs/<parameter>/<item id>/`` and
waits until every item it needs is done (``partial_ok=True`` runs it with the done ones). A fork with
``refresh={"script": ["02"]}`` reruns lesson 02 and every final step that uses it, and copies the rest.

Why: without a fan-in step, work that combines items happens after ``wf.run`` returns, outside the run
folder: no input snapshot, no timing, and a fork that refreshes one lesson does not rebuild the workbook.
"""

import tempfile
from pathlib import Path

import hone_flow as fk

calls: list[str] = []
wf = fk.Workflow("course", storage=tempfile.mkdtemp())


@wf.step()
def script(topic: str, ctx: fk.Context) -> str:
    calls.append(f"script/{ctx.item_id}")
    return f"Lesson on {topic}."


@wf.step()
def quiz(script: str) -> list[str]:
    return [f"What is the point of: {script}"]


@wf.final_step()
def workbook(script: dict[str, str], quiz: dict[str, list[str]], ctx: fk.Context) -> fk.File:
    calls.append("workbook")
    path = ctx.new_file("workbook.md")
    parts = [f"## {lesson}\n{text}\n- {quiz[lesson][0]}" for lesson, text in script.items()]
    path.write_text("\n\n".join(parts))
    return fk.File(path)


items = [fk.Item("01", {"topic": "listening"}), fk.Item("02", {"topic": "feedback"})]
run = wf.run(items)
print(run.output("workbook").path.read_text())
assert calls == ["script/01", "script/02", "workbook"]
assert Path(run.location, "workbook/inputs/script/02/script.json").is_file()

calls.clear()
refreshed = run.fork(refresh={"script": ["02"]})  # one lesson again; the workbook follows
print(calls)
assert calls == ["script/02", "workbook"]
assert refreshed.steps("script", "01")[0].labels == ["reused"]
