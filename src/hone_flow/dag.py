"""Steps, their parameters, and the graph inferred from parameter names."""

from __future__ import annotations

import hashlib
import inspect
import typing
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from hone_flow.errors import WorkflowDefinitionError
from hone_flow.types import GLOBAL_ITEM, PARAM_MARKER, Context, Item, Items

RUN_LEVEL = ("global", "final", "select")  # step kinds with one unit per run (item None)
FAN_IN = ("final", "select")  # run-level steps that see every item's output (changes 0004, 0007)
EDGES = ("step", "items")  # argument kinds that are edges of the graph
REVIEW_NOTE = "review_note"
PREVIOUS = "previous"  # a revised step's rejected (or replaced) output (change 0006)


@dataclass(frozen=True)
class Arg:
    """One step parameter and where its value comes from.

    ``kind``: ctx, item, param, review_note, previous, step (another step's output), items (an item step's
    output for every item, in a fan-in step), input (an item input) or ref (a name not resolved yet;
    becomes step, items or input in ``build_graph``).
    """

    name: str
    kind: str
    has_default: bool = False
    default: Any = None
    source: str = ""  # producing step, for kinds "step" and "items"


@dataclass(frozen=True)
class Step:
    name: str
    fn: Callable[..., Any]
    kind: str  # "step", "gate", "global", "final" or "select"
    version: str
    resources: str
    deterministic: bool
    outputs: tuple[str, ...]
    vram_gb: float
    source_hash: str
    args: tuple[Arg, ...]
    partial_ok: bool = False  # a fan-in step that also runs when some items are not done
    per: str | None = None  # an item step over the items this global step produces (design §4.20)
    produces_items: bool = False  # a global step annotated to return fk.Items

    @property
    def once(self) -> bool:
        """One unit per run (a global, final or select step), not one per item."""
        return self.kind in RUN_LEVEL


def source_hash(fn: Callable[..., Any]) -> str:
    """sha256 of the function's source; empty when the source is not available (e.g. a REPL)."""
    try:
        return hashlib.sha256(inspect.getsource(fn).encode()).hexdigest()
    except (OSError, TypeError):
        return ""


def _classify(param: inspect.Parameter, hint: Any) -> Arg:
    has_default = param.default is not inspect.Parameter.empty
    default = param.default if has_default else None
    if hint is Context:
        kind = "ctx"
    elif hint is Item:
        kind = "item"
    elif typing.get_origin(hint) is typing.Annotated and PARAM_MARKER in hint.__metadata__:
        kind = "param"
    elif param.name in (REVIEW_NOTE, PREVIOUS):
        kind = param.name
    else:
        kind = "ref"
    return Arg(param.name, kind, has_default, default)


def make_step(
    fn: Callable[..., Any],
    kind: str,
    *,
    version: str,
    resources: str = "cpu",
    deterministic: bool = True,
    outputs: Sequence[str] | None = None,
    vram_gb: float = 0.0,
    partial_ok: bool = False,
    per: str | None = None,
) -> Step:
    """Read a step function's signature into a ``Step``."""
    name = fn.__name__
    try:
        hints = typing.get_type_hints(fn, include_extras=True)
    except Exception as exc:
        raise WorkflowDefinitionError(f"cannot resolve the type hints of step {name!r}: {exc}") from exc
    args: list[Arg] = []
    for param in inspect.signature(fn).parameters.values():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            raise WorkflowDefinitionError(
                f"step {name!r}: *args / **kwargs are not supported; name each input"
            )
        args.append(_classify(param, hints.get(param.name)))
    return Step(
        name=name,
        fn=fn,
        kind=kind,
        version=str(version),
        resources=resources,
        deterministic=deterministic,
        outputs=tuple(outputs) if outputs else (name,),
        vram_gb=vram_gb,
        source_hash=source_hash(fn),
        args=tuple(args),
        partial_ok=partial_ok,
        per=per,
        produces_items=hints.get("return") is Items,
    )


@dataclass(frozen=True)
class Graph:
    """Validated steps in topological order (definition order where free)."""

    steps: dict[str, Step]
    producers: dict[str, str]  # output name -> step name

    def deps(self, step: str) -> list[str]:
        return list(dict.fromkeys(a.source for a in self.steps[step].args if a.kind in EDGES))

    def downstream(self, names: Iterable[str]) -> set[str]:
        """The named steps and every step that depends on them."""
        found = set(names)
        for step in self.steps:  # topological order: deps are seen before dependants
            if found.intersection(self.deps(step)):
                found.add(step)
        return found

    def upstream(self, names: Iterable[str]) -> set[str]:
        """The named steps and every step they depend on (a producer counts, design §4.20)."""
        found = set(names)
        for step in reversed(self.steps):
            if step in found:
                found.update(self.deps(step))
                producer = self.steps[step].per
                found.update([producer] if producer is not None else [])
        return found


def _resolve(step: Step, producers: Mapping[str, str], steps: Mapping[str, Step]) -> Step:
    args: list[Arg] = []
    for raw in step.args:
        if raw.kind != "ref":
            arg = raw
        elif raw.name in producers:
            source = steps[producers[raw.name]]
            kind = "items" if step.kind in FAN_IN and not source.once else "step"
            arg = Arg(raw.name, kind, raw.has_default, raw.default, source.name)
        else:
            arg = Arg(raw.name, "input", raw.has_default, raw.default)
        _check_arg(step, arg, steps)
        args.append(arg)
    if step.kind == "gate" and not any(a.kind == "step" for a in args):
        raise WorkflowDefinitionError(f"gate {step.name!r} must take the output of the step it reviews")
    return replace(step, args=tuple(args))


def _check_arg(step: Step, arg: Arg, steps: Mapping[str, Step]) -> None:
    """Where a parameter may come from, by step kind (design §4.1, §4.18)."""
    source = steps[arg.source] if arg.kind in EDGES else None
    if step.kind == "global" and (arg.kind in ("item", "input") or (source and source.kind != "global")):
        raise WorkflowDefinitionError(
            f"global step {step.name!r} parameter {arg.name!r} cannot be resolved: global steps may "
            f"only use fk.Context, fk.Param[...] and other global steps (known steps: {sorted(steps)})"
        )
    if step.kind in FAN_IN and arg.kind in ("item", "input"):
        raise WorkflowDefinitionError(
            f"{step.kind} step {step.name!r} parameter {arg.name!r} cannot be resolved: it runs once over "
            f"all items, so it takes step outputs (a dict by item id for item steps), fk.Param[...] and "
            f"fk.Context, not item inputs (known steps: {sorted(steps)})"
        )
    if not step.once and source is not None and source.kind == "final":
        raise WorkflowDefinitionError(
            f"step {step.name!r} cannot take {arg.name!r}: final step {source.name!r} runs after every "
            "item; only other final steps can take its output"
        )


def _check_group(step: Step, steps: Mapping[str, Step]) -> None:
    """``per=`` names a global step returning ``fk.Items``; an item step only takes item steps over the
    same items (design §4.20)."""
    if step.per is not None:
        producer = steps.get(step.per)
        if producer is None or producer.kind != "global" or not producer.produces_items:
            raise WorkflowDefinitionError(
                f"step {step.name!r} has per={step.per!r}, which must name a @wf.global_step annotated "
                "to return fk.Items"
            )
    for arg in step.args:
        source = steps[arg.source] if arg.kind == "step" else None
        if not step.once and source is not None and not source.once and source.per != step.per:
            raise WorkflowDefinitionError(
                f"step {step.name!r} (items of {step.per or 'the run'}) cannot take {arg.name!r} from step "
                f"{source.name!r} (items of {source.per or 'the run'}): the items differ; use the same per="
            )


def _topological(steps: Mapping[str, Step]) -> list[str]:
    order: list[str] = []
    visiting: list[str] = []

    def visit(name: str) -> None:
        if name in order:
            return
        if name in visiting:
            cycle = [*visiting[visiting.index(name) :], name]
            raise WorkflowDefinitionError(f"steps form a cycle: {' -> '.join(cycle)}")
        visiting.append(name)
        if steps[name].per in steps:  # a producer runs before the steps over its items
            visit(steps[name].per or "")
        for arg in steps[name].args:
            if arg.kind in EDGES:
                visit(arg.source)
        visiting.pop()
        order.append(name)

    for name in steps:
        visit(name)
    return order


def build_graph(steps: Mapping[str, Step]) -> Graph:
    """Resolve every parameter and order the steps; raise ``WorkflowDefinitionError`` on problems."""
    if not steps:
        raise WorkflowDefinitionError("the workflow has no steps; add one with @wf.step()")
    producers = {out: s.name for s in steps.values() for out in s.outputs}
    resolved = {name: _resolve(s, producers, steps) for name, s in steps.items()}
    for step in resolved.values():
        _check_group(step, resolved)
    return Graph({name: resolved[name] for name in _topological(resolved)}, producers)


def check_items(graph: Graph, items: Sequence[Item], *, per: str | None = None) -> None:
    """Every item input a step needs must be present (or have a default). ``per``: the items of that
    producing step (checked for the steps declared ``per=`` it), else the run's own items."""
    ids = [item.id for item in items]
    if len(set(ids)) != len(ids):
        raise WorkflowDefinitionError(f"item ids must be unique; got {ids}")
    bad = [i for i in ids if not i or i in (".", "..", GLOBAL_ITEM) or "/" in i or "\\" in i]
    if bad:  # ids name folders
        raise WorkflowDefinitionError(
            f"item ids {bad} are not allowed: use non-empty names without '/' or '\\', not '.', '..' "
            f"or {GLOBAL_ITEM!r}"
        )
    for item in items:
        for step in graph.steps.values():
            if step.per != per:  # produced items are checked when their producer makes them
                continue
            for arg in step.args:
                if arg.kind == "input" and not arg.has_default and arg.name not in item.inputs:
                    raise WorkflowDefinitionError(
                        f"step {step.name!r} parameter {arg.name!r} is not a step output, a fk.Param, "
                        f"or an input of item {item.id!r} (item inputs: {sorted(item.inputs)}; step "
                        f"outputs: {sorted(graph.producers)}). Fix the parameter name or add the input."
                    )
