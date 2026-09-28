"""Which units a call visits and what happens to each (design §4.6, §4.18): the units of a step, the
phases of a depth-first walk, and the decision per unit (run it, keep it, or give it a state).

A *unit* is one step for one item (``item=None`` for a run-level step: global, final or select).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hone_flow.dag import FAN_IN, Graph, Step
from hone_flow.run_format import RunFolder, StepMetadata, state_key
from hone_flow.types import Selection

if TYPE_CHECKING:
    from hone_flow.executor import Call

NEEDS_WORK = ("pending", "failed", "interrupted", "blocked", "skipped")


def item_ids(call: Call, step: Step) -> list[str]:
    """The items an item step runs for, in manifest order: the run's own, or those its ``per`` step
    produced (design §4.20)."""
    return [item.id for item in call.manifest.items if item.items_from == step.per]


def units(call: Call, step: Step, only: str | None) -> list[str | None]:
    """The units of ``step`` to visit: ``[None]`` for a run-level step, else its items (or just ``only``)."""
    if step.once:
        return [None]
    ids = item_ids(call, step)
    if only is None:
        return list(ids)
    return [only] if only in ids else []


def phases(graph: Graph) -> list[list[Step]]:
    """The steps split at every fan-in step: a depth-first walk takes each item through one phase, then
    runs the fan-in step that needs all of them, then goes on."""
    found: list[list[Step]] = [[]]
    for step in graph.steps.values():
        if step.kind in FAN_IN:
            found += [[step], []]
        else:
            found[-1].append(step)
    return [phase for phase in found if phase]


def decide(call: Call, step: Step, item: str | None) -> str:
    """``run``, ``keep`` (nothing to do), or the state the unit gets without running."""
    state = call.manifest.state.get(state_key(step.name, item), "pending")
    if state not in NEEDS_WORK:
        return "keep"
    if step.name not in call.steps or (
        item is not None and call.items is not None and item not in call.items
    ):
        return "skipped" if state in ("pending", "skipped") else "keep"
    graph = call.wf.graph()
    run_level: list[str] = []
    per_item: list[str] = []
    for dep in graph.deps(step.name):
        source = graph.steps[dep]
        if source.per is not None:  # no items before their producer is done (design §4.20)
            run_level.append(_state(call, source.per, None))
        if source.once:
            run_level.append(_state(call, dep, None))
        else:
            per_item += [
                _state(call, dep, i) for i in ([item] if item is not None else item_ids(call, source))
            ]
    if step.kind in FAN_IN:
        return _fan_in(step, run_level, [s for s in per_item if s != "not_selected"])
    decision = from_upstream(run_level + per_item)
    if decision != "blocked" and ("not_selected" in per_item or not _selected(call, step, item or "")):
        return "not_selected"  # a failure upstream wins: the item did not get a fair chance
    return decision


def _selected(call: Call, step: Step, item: str) -> bool:
    """False when a select step this item step takes has run and left the item out (design §4.19)."""
    graph = call.wf.graph()
    for dep in graph.deps(step.name):
        if graph.steps[dep].kind == "select" and _state(call, dep, None) == "done":
            meta = call.folder.read_meta(dep, None)
            if meta is not None and item not in selection(call.folder, meta).keep:
                return False
    return True


def selection(folder: RunFolder, meta: StepMetadata) -> Selection:
    """The ``Selection`` a select step stored."""
    output = meta.outputs[meta.step]
    data = folder.storage.read_bytes(folder.step_key(meta.step, None, "output", next(iter(output.files))))
    return Selection.model_validate_json(data)


def _state(call: Call, step: str, item: str | None) -> str:
    return call.manifest.state.get(state_key(step, item), "pending")


def from_upstream(states: list[str]) -> str:
    """A unit whose inputs are these states: ``blocked`` after a failure, ``pending`` while waiting (e.g.
    for a person at a gate), else ``run``."""
    if any(s in ("failed", "blocked") for s in states):
        return "blocked"
    if any(s != "done" for s in states):
        return "pending"
    return "run"


def _fan_in(step: Step, run_level: list[str], per_item: list[str]) -> str:
    """A fan-in step waits for every item it needs (design §4.18), unless ``partial_ok``."""
    decision = from_upstream(run_level)
    if decision != "run" or step.partial_ok:
        return decision
    if "skipped" in per_item and not any(s in ("failed", "blocked") for s in per_item):
        return "skipped"  # items left out by the call's selection: resume does it later
    return from_upstream(per_item)
