"""The fork diff: fork() works out what changed since a run, and says why it reruns each step.

What: ``run.fork()`` without ``refresh`` compares every step of the source run with the current
workflow: step version, source hash, the params the step declares, and the content of the item files it
reads. Changed steps and their downstream run; the rest is reused. ``fork(dry_run=True)`` returns the
plan (``fk.ForkPlan``) without writing anything.

How: change something (here: a param and one input file), call ``run.fork(dry_run=True)`` to review the
plan, then ``run.fork()`` to execute exactly that plan. Reasons include ``version_changed 1->2``,
``source_changed``, ``param_changed:<name>``, ``input_changed:<name>``, ``new_item``,
``not_done_in_source`` and ``downstream_of:<step>``.

Why: incremental reruns without a global cache: reuse only happens inside a run's family tree, and every
reuse or rerun is explained in the new run's ``manifest.json`` (``fork_of.plan``).
"""

import tempfile
from pathlib import Path

import hone_flow as fk

tmp = Path(tempfile.mkdtemp(prefix="hone-flow-example-"))
(tmp / "01.md").write_text("rain on the roof\n")
(tmp / "02.md").write_text("sun on the sea\n")
wf = fk.Workflow("diff_demo", storage=tmp / "flows")


@wf.step()
def timeline(lyrics: fk.File) -> list[str]:
    return lyrics.path.read_text().split()


@wf.step()
def styled(timeline: list[str], style: fk.Param[str]) -> list[str]:
    return [f"{word}/{style}" for word in timeline]


@wf.step()
def render(styled: list[str]) -> str:
    return " ".join(styled)


items = [fk.Item("01", {"lyrics": fk.File(tmp / "01.md")}), fk.Item("02", {"lyrics": fk.File(tmp / "02.md")})]
source = wf.run(items, params={"style": "noir"})

(tmp / "01.md").write_text("snow on the roof\n")  # an input file changed for item 01
plan = source.fork(params={"style": "noir"}, dry_run=True)
for row in plan.rows:
    print(f"{row.action:5} {row.step}/{row.item}: {row.reason}")
assert [r.reason for r in plan.rows if r.item == "02"] == ["unchanged"] * 3
assert {(r.step, r.reason) for r in plan.rows if r.item == "01"} == {
    ("timeline", "input_changed:lyrics"),
    ("styled", "downstream_of:timeline"),
    ("render", "downstream_of:timeline"),
}

plan = source.fork(params={"style": "bright"}, dry_run=True)  # a param only `styled` declares
assert {(r.step, r.item): r.reason for r in plan.rows}[("styled", "02")] == "param_changed:style"
new = source.fork(params={"style": "bright"})
print(new.output("render", "01"), "|", new.output("render", "02"))
assert new.output("render", "01") == "snow/bright on/bright the/bright roof/bright"
assert new.manifest["fork_of"]["plan"] == [
    {"item": r.item, "step": r.step, "action": r.action, "reason": r.reason} for r in plan.rows
]
