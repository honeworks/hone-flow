"""Fork (design §4.7): a new run beside the source that copies what did not change and reruns the rest.

For every (step, item) of the new run, the *fork diff* decides ``reuse`` or ``run`` and says why. The
source is only read; reused results are copied (not referenced), so the fork is self-contained.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hone_flow._records import strip_secrets
from hone_flow._tracing import incoming_trace, new_span_id, parent_of
from hone_flow.dag import FAN_IN, RUN_LEVEL, Graph, Step
from hone_flow.errors import HoneFlowError, WorkflowDefinitionError
from hone_flow.executor import Call, execute, open_call
from hone_flow.progress import OnEvent
from hone_flow.run_format import (
    ForkInfo,
    ItemInfo,
    ItemInput,
    Manifest,
    PlanRowInfo,
    RunFolder,
    StepMetadata,
    apply_label,
    digest,
    dir_files,
    file_info,
    item_from_info,
    item_info,
    new_run_id,
    state_key,
)
from hone_flow.run_lease import RunLease
from hone_flow.serialize import canonical_json
from hone_flow.spans import unit_span
from hone_flow.types import ForkPlan, ForkPlanRow, Item

if TYPE_CHECKING:
    from hone_flow.run import Run
    from hone_flow.workflow import Workflow

KEPT_LABELS = ("approved", "edited")  # a reused gate keeps its decision
RefreshArg = Sequence[str] | str | Mapping[str, Sequence[str] | None]
Refresh = dict[str, set[str] | None]  # step -> item ids to refresh; None: every item


def fork(
    run: Run,
    refresh: RefreshArg = (),
    *,
    items: Sequence[str | Item] | None = None,
    params: Mapping[str, Any] | None = None,
    dry_run: bool = False,
    trace: Mapping[str, str] | None = None,
    label: str | None = None,
    description: str | None = None,
    on_event: OnEvent | None = None,
) -> Run | ForkPlan:
    """Plan the fork of ``run`` and, unless ``dry_run``, create and execute the new run.

    The fork's label defaults to ``"<source label> (fork)"`` and its description to the source's.
    """
    wf = run.require_workflow("fork", archive_ok=True)
    source = run.read_manifest()
    records = fork_items(run.folder, source, items)
    wanted = refresh_map(wf, refresh, [r.id for r in records])
    merged = {**source.params, **dict(params or {})}
    wf.check_call([item_from_info(r) for r in records if r.items_from is None], merged, None)
    rows = plan(wf, run.folder, source, items=records, params=merged, refresh=wanted)
    if dry_run:
        return ForkPlan(source.run_id, tuple(rows))
    naming = (
        label if label is not None else (f"{source.label} (fork)" if source.label else None),
        description if description is not None else source.description,
    )
    return start_fork(
        run, source, records=records, params=merged, rows=rows, trace=trace, naming=naming, on_event=on_event
    )


def start_fork(
    run: Run,
    source: Manifest,
    *,
    records: list[ItemInfo],
    params: dict[str, Any],
    rows: list[ForkPlanRow],
    trace: Mapping[str, str] | None,
    naming: tuple[str | None, str | None] = (None, None),
    on_event: OnEvent | None = None,
) -> Run:
    """Create the fork's run folder, copy the ``reuse`` rows and execute the ``run`` rows."""
    from hone_flow.run import Run  # noqa: PLC0415 - run.py imports this module

    wf = run.require_workflow("fork", archive_ok=True)
    folder = RunFolder(run.folder.storage, f"{wf.name}/runs/{new_run_id()}")
    incoming = incoming_trace(trace)
    manifest = wf.new_manifest(folder, records, params, source.seed, trace_id=parent_of(incoming)[0])
    manifest.fork_of = ForkInfo(run_id=source.run_id, plan=[PlanRowInfo(**asdict(row)) for row in rows])
    apply_label(manifest, *naming)
    with RunLease(folder.storage, folder.prefix):
        folder.write_manifest(manifest, create=True)
        call = open_call(wf, folder, kind="fork", params=params, trace=incoming, on_event=on_event)
        if source.calls and source.calls[0].span_id:  # link the fork's trace to the source's
            call.links.append(
                {
                    "trace_id": source.trace_id,
                    "span_id": source.calls[0].span_id,
                    "attributes": {"hone.flow.fork_of": source.run_id},
                }
            )
        for row in rows:
            if row.action == "reuse" and row.reason == "not_selected":
                call.manifest.state[state_key(row.step, row.item)] = "not_selected"
            elif row.action == "reuse":
                copy_reused(run.folder, call, wf.graph().steps[row.step], row.item)
        execute(call)
    return Run(folder.storage, folder.prefix, wf)


def refresh_map(wf: Workflow, refresh: RefreshArg, item_ids: list[str]) -> Refresh:
    """``refresh`` as step -> the item ids to refresh (``None``: every item). It may be a step name, a
    sequence of names, or a mapping from step name to item ids (``{"script": ["02"]}``)."""
    steps = wf.graph().steps
    if isinstance(refresh, str):
        refresh = (refresh,)
    wanted: Refresh = (
        {name: set(ids) if ids is not None else None for name, ids in refresh.items()}
        if isinstance(refresh, Mapping)
        else dict.fromkeys(refresh)
    )
    if unknown := [name for name in wanted if name not in steps]:
        raise WorkflowDefinitionError(f"refresh names unknown steps {unknown}; steps: {list(steps)}")
    for name, ids in wanted.items():
        if missing := sorted((ids or set()) - set(item_ids)):
            raise WorkflowDefinitionError(
                f"refresh of {name!r} names unknown items {missing}; items: {item_ids}"
            )
    return wanted


def fork_items(folder: RunFolder, source: Manifest, items: Sequence[str | Item] | None) -> list[ItemInfo]:
    """The fork's items: the source's (by id, files re-hashed at their original paths) or new ``fk.Item``s."""
    recorded = {i.id: i for i in source.items}
    chosen: list[ItemInfo] = []
    for entry in items if items is not None else list(recorded):
        if isinstance(entry, Item):
            chosen.append(item_info(entry))
        elif entry in recorded:
            chosen.append(_rehash(folder, source, recorded[entry]))
        else:
            raise WorkflowDefinitionError(
                f"run {source.run_id} has no item {entry!r}; pass fk.Item(...) to add one"
            )
    return chosen


def _rehash(folder: RunFolder, source: Manifest, info: ItemInfo) -> ItemInfo:
    """A recorded item with its files hashed again. A file or folder that is gone counts as unchanged,
    and a step that needs it gets the source run's snapshot of it (``snapshot``)."""
    inputs: dict[str, ItemInput] = {}
    for name, record in info.inputs.items():
        path = Path(record.path or "")
        if record.kind == "file" and path.is_file():
            found = file_info(path)
            inputs[name] = record.model_copy(update={"sha256": found.sha256, "size": found.size})
        elif record.kind == "dir" and path.is_dir():
            files = dir_files(path)
            size = sum(f.size for f in files.values())
            inputs[name] = record.model_copy(update={"sha256": digest(files), "size": size})
        elif record.kind != "json" and record.snapshot is None:  # a fork of a fork keeps its snapshot
            inputs[name] = record.model_copy(
                update={"snapshot": _snapshot(folder, source, info.id, name, record)}
            )
        else:
            inputs[name] = record
    return info.model_copy(update={"inputs": inputs})


def _snapshot(folder: RunFolder, source: Manifest, item: str, name: str, record: ItemInput) -> str | None:
    """Where the source run keeps its copy of an item input (a key, or a folder prefix for a dir)."""
    for info in source.steps:
        meta = folder.read_meta(info.name, item) if info.kind not in RUN_LEVEL else None
        if meta is not None and name in meta.inputs and meta.inputs[name].source == "item_input":
            first = next(iter(meta.inputs[name].files))
            inputs = folder.step_key(info.name, item, "inputs")
            return f"{inputs}/{first}" if record.kind == "file" else f"{inputs}/{name}"
    return None


def plan(
    wf: Workflow,
    source_folder: RunFolder,
    source: Manifest,
    *,
    items: list[ItemInfo],
    params: Mapping[str, Any],
    refresh: Refresh,
) -> list[ForkPlanRow]:
    """The fork diff: one row per (step, item) of the new run, in execution order."""
    graph = wf.graph()
    rows: list[ForkPlanRow] = []
    origins: dict[tuple[str, str | None], list[str]] = {}  # the steps whose own changes make a unit run
    same_items = [i.id for i in items] == [i.id for i in source.items]  # produced items included
    for step in graph.steps.values():
        for info in [None] if step.once else [i for i in items if i.items_from == step.per]:
            item = info.id if info is not None else None
            meta = (
                source_folder.read_meta(step.name, item)
                if state_key(step.name, item) in source.state
                else None
            )
            own = _changes(wf, source, step, info, meta, params=params, refresh=refresh)
            if step.kind in FAN_IN and not same_items and "new_step" not in own:
                own.append("items_changed")
            upstream = _upstream(graph, origins, step.name, item, items)
            left_out = source.state.get(state_key(step.name, item)) == "not_selected"
            if left_out and own == ["not_done_in_source"] and not upstream:
                own = []  # the selection that left it out is reused: it stays out
            origins[(step.name, item)] = [step.name] if own else upstream
            why = own + [f"downstream_of:{name}" for name in upstream]
            unchanged = "not_selected" if left_out else "unchanged"
            rows.append(ForkPlanRow(item, step.name, "run" if why else "reuse", "; ".join(why) or unchanged))
    return rows


def _upstream(
    graph: Graph,
    origins: dict[tuple[str, str | None], list[str]],
    step: str,
    item: str | None,
    items: list[ItemInfo],
) -> list[str]:
    """The steps that run for a reason of their own upstream of this unit (a fan-in step: of any item)."""
    found: list[str] = []
    for dep in graph.deps(step):
        if graph.steps[dep].once:
            found += origins[(dep, None)]
        else:
            group = [i.id for i in items if i.items_from == graph.steps[dep].per]
            for other in [item] if item is not None else group:
                found += origins.get((dep, other), [])
    return list(dict.fromkeys(found))


def _changes(
    wf: Workflow,
    source: Manifest,
    step: Step,
    info: ItemInfo | None,
    meta: StepMetadata | None,
    *,
    params: Mapping[str, Any],
    refresh: Refresh,
) -> list[str]:
    """Why this step must run, from its own definition, params and inputs (not its upstream)."""
    requested = step.name in refresh and (
        step.once
        or refresh[step.name] is None
        or (info is not None and info.id in (refresh[step.name] or ()))
    )
    why = ["refresh_requested"] if requested else []
    if meta is not None and meta.status == "done":
        if meta.version != step.version:
            why.append(f"version_changed {meta.version}->{step.version}")
        elif meta.source_hash and step.source_hash and meta.source_hash != step.source_hash:
            why.append("source_changed")
        declared = {a.name: params.get(a.name, a.default) for a in step.args if a.kind == "param"}
        why += [
            f"param_changed:{n}"
            for n, v in declared.items()
            if not _same(strip_secrets(v), meta.params.get(n))
        ]
        why += [f"input_changed:{n}" for n in _changed_inputs(source, step, info)]
    if wf.version != source.workflow_version:
        why.append(f"workflow_version_changed {source.workflow_version}->{wf.version}")
    if step.name not in {s.name for s in source.steps}:
        why.append("new_step")
    elif info is not None and info.id not in {i.id for i in source.items}:
        why.append("new_item")
    elif meta is None or meta.status != "done":
        why.append("not_done_in_source")
    return why


def _changed_inputs(source: Manifest, step: Step, info: ItemInfo | None) -> list[str]:
    """Item inputs the step reads (by name, or all of them through ``fk.Item``) whose content differs."""
    if info is None:
        return []
    source_item = next((i for i in source.items if i.id == info.id), None)
    if source_item is None:
        return []
    before = source_item.inputs
    names = [a.name for a in step.args if a.kind == "input"]
    if any(a.kind == "item" for a in step.args):
        names = sorted(set(before) | set(info.inputs))
    return [n for n in names if _content(before.get(n)) != _content(info.inputs.get(n))]


def _same(a: Any, b: Any) -> bool:
    """Equal as recorded JSON (a tuple and a list with the same values are the same param)."""
    return canonical_json(a) == canonical_json(b)


def _content(record: ItemInput | None) -> Any:
    if record is None:
        return None
    return record.value if record.kind == "json" else record.sha256


def copy_reused(source_folder: RunFolder, call: Call, step: Step, item: str | None) -> None:
    """Copy a source step's ``inputs/`` and ``output/`` into the fork and write its metadata anew."""
    meta = source_folder.read_meta(step.name, item)  # first: only a committed result is copied
    if meta is None or meta.status != "done":
        raise HoneFlowError(
            f"step {step.name!r} of item {item!r} changed in {source_folder.location} while "
            "it was forked; fork again"
        )
    storage, folder = call.folder.storage, call.folder
    src, dst = source_folder.step_key(step.name, item), folder.step_key(step.name, item)
    for part in ("inputs", "output"):
        for key in storage.list(f"{src}/{part}"):
            storage.copy(key, dst + key.removeprefix(src))
    meta = meta.model_copy(
        update={
            "run_id": call.manifest.run_id,
            "labels": ["reused", *[label for label in meta.labels if label in KEPT_LABELS]],
            "reused_from": source_folder.prefix.rsplit("/", 1)[-1],
            "source_attempt": meta.attempt,
            "attempt": 1,
            "attempts": [],
            "span_id": new_span_id(),
        }
    )
    folder.write_meta(meta)
    call.manifest.state[state_key(step.name, item)] = "done"
    call.spans.emit(unit_span(call, step, item, "done", meta=meta))
