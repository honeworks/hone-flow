"""Items made by a step (design §4.20, change 0005): a global step returns ``fk.Items`` and the steps
declared ``per=`` it run once per produced item.

Produced items join the manifest's ``items`` with ``items_from: <step>`` when the producer commits. When
the producer runs again (a retry after a rejection, or in a fork), items with the same id and inputs keep
their work; an item whose inputs changed starts over (its results are retired as ``replaced``); an item
no longer produced is removed with its step folders. Fan-in steps over them start over too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hone_flow.commit import retire
from hone_flow.dag import Step, check_items
from hone_flow.errors import HoneFlowError
from hone_flow.run_format import PlanRowInfo, item_info, state_key
from hone_flow.types import Dir, File, Items

if TYPE_CHECKING:
    from hone_flow.executor import Call


def check_produced(call: Call, step: Step, result: Any) -> Items:
    """The producer's return value, checked before it is committed (a problem fails the step)."""
    if not isinstance(result, Items):
        raise HoneFlowError(
            f"step {step.name!r} is annotated to return fk.Items; it returned {type(result).__name__}"
        )
    taken = {i.id for i in call.manifest.items if i.items_from != step.name}
    if clash := sorted({i.id for i in result} & taken):
        raise HoneFlowError(f"step {step.name!r} produced items {clash}, which are already items of the run")
    for item in result:
        if any(isinstance(value, File | Dir) for value in item.inputs.values()):
            raise HoneFlowError(
                f"produced item {item.id!r} has a file or folder input: produced items take JSON values; "
                "pass files through step outputs instead"
            )
    check_items(call.wf.graph(), result, per=step.name)
    return result


def add_produced(call: Call, step: Step, items: Items) -> None:
    """Record the produced items in the manifest (the caller writes it with the producer's state)."""
    manifest = call.manifest
    graph = call.wf.graph()
    consumers = [s.name for s in graph.steps.values() if s.per == step.name]
    old = {i.id: i for i in manifest.items if i.items_from == step.name}
    new = [item_info(i).model_copy(update={"items_from": step.name}) for i in items]
    changed = False
    for info in new:
        before = old.pop(info.id, None)
        if before is not None and before.inputs == info.inputs:
            continue
        changed = True
        for name in consumers:
            _start_over(call, name, info.id, "new_item" if before is None else "produced_item_changed")
    for gone in old:
        changed = True
        for name in consumers:
            _remove(call, name, gone)
    manifest.items = [i for i in manifest.items if i.items_from != step.name] + new
    if changed:
        fan_in = [n for n in graph.downstream(consumers) if graph.steps[n].once and n not in consumers]
        for name in fan_in:
            _start_over(call, name, None, f"downstream_of:{step.name}")


def _start_over(call: Call, step: str, item: str | None, reason: str) -> None:
    meta = call.folder.read_meta(step, item)
    if meta is not None and meta.outputs:
        retire(call.folder, meta, "replaced")
    call.manifest.state[state_key(step, item)] = "pending"
    _plan_row(call, step, item, reason)


def _remove(call: Call, step: str, item: str) -> None:
    call.folder.storage.delete(call.folder.step_key(step, item))
    call.manifest.state.pop(state_key(step, item), None)
    if call.manifest.fork_of is not None:
        plan = call.manifest.fork_of.plan
        plan[:] = [r for r in plan if (r.step, r.item) != (step, item)]


def _plan_row(call: Call, step: str, item: str | None, reason: str) -> None:
    """A fork's recorded plan follows what it did: the row of a unit that starts over says why."""
    fork = call.manifest.fork_of
    if fork is None:
        return
    row = PlanRowInfo(item=item, step=step, action="run", reason=reason)
    for index, existing in enumerate(fork.plan):
        if (existing.step, existing.item) == (step, item):
            if existing.action == "reuse":
                fork.plan[index] = row
            return
    fork.plan.append(row)
