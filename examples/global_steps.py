"""Global steps: work done once per run and shared by every item.

What: a ``@wf.global_step`` runs once per run (not per item); item steps take its output by naming it.

How: decorate the shared step with ``@wf.global_step()``. It may use params, the context and other
global steps, but not item inputs. Item steps name it like any other step output.

Why: a style guide, a loaded model config or a shared prompt should be computed once and be the same
for every item. In the run folder it sits at ``<step>/`` (not ``<step>/item_<id>/``); if it fails,
every item step that uses it is ``blocked``.
"""

import tempfile
from pathlib import Path

import hone_flow as fk

calls: list[str] = []
wf = fk.Workflow("globals_demo", storage=tempfile.mkdtemp())


@wf.global_step()
def style_guide(style: fk.Param[str]) -> dict[str, object]:
    calls.append("style_guide")
    return {"style": style, "palette": ["black", "amber"]}


@wf.step()
def shot(line: str, style_guide: dict[str, object], ctx: fk.Context) -> str:
    calls.append(f"shot/{ctx.item_id}")
    return f"{line} [{style_guide['style']}]"


items = [fk.Item(f"0{i}", {"line": line}) for i, line in enumerate(["dawn", "noon", "dusk"], 1)]
run = wf.run(items, params={"style": "silhouette"})
print(calls)
print(run.output("style_guide"), run.output("shot", "03"))

assert calls.count("style_guide") == 1  # once for the whole run
assert run.output("shot", "02") == "noon [silhouette]"
assert Path(run.location, "style_guide/output/style_guide.json").is_file()
assert Path(run.location, "shot/item_01/inputs/style_guide.json").is_file()  # each item keeps a copy
