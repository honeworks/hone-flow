"""A step's inputs (design §4.6): each step output and item input it takes is snapshotted into the run
folder's ``inputs/`` and fetched into the attempt's local work folder, so the step sees local paths
and the run folder keeps a copy of exactly what the step received.
"""

from __future__ import annotations

import logging
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from hone_flow._tracing import traceparent
from hone_flow.dag import Arg, Step
from hone_flow.errors import HoneFlowError
from hone_flow.run_format import (
    FileInfo,
    InputInfo,
    ItemInfo,
    OutputInfo,
    StepMetadata,
    digest,
    dir_files,
    file_info,
    load_output,
    state_key,
)
from hone_flow.schedule import item_ids
from hone_flow.serialize import canonical_json, sha256_bytes
from hone_flow.storage import download_tree, upload_tree
from hone_flow.types import GLOBAL_ITEM, Context, Dir, File, Item

if TYPE_CHECKING:
    from hone_flow.executor import Call


def step_trace(call: Call, step: Step, item: str | None, meta: StepMetadata) -> dict[str, str]:
    """The trace context a step runs in: under its own span, with the run, item and step ids."""
    trace = call.shared | {
        "traceparent": traceparent(call.manifest.trace_id, meta.span_id or ""),
        "hone.step": step.name,
    }
    return trace if item is None else trace | {"hone.item": item}


def prepare_inputs(
    call: Call, step: Step, item: str | None, meta: StepMetadata, workdir: Path
) -> dict[str, Any]:
    """The step's arguments; step outputs and item inputs are snapshotted into ``inputs/`` first."""
    inputs_key = call.folder.step_key(step.name, item, "inputs")
    call.folder.storage.delete(inputs_key)  # a retry starts from fresh snapshots
    item_inputs = _item_info(call, item).inputs if item is not None else {}
    args: dict[str, Any] = {}
    for arg in step.args:
        if arg.kind == "ctx":
            args[arg.name] = context(call, step, item, meta, workdir / "work")
        elif arg.kind == "item" and item is not None:
            args[arg.name] = item_arg(call, item, inputs_key, workdir / "inputs", meta)
        elif arg.kind == "param":
            args[arg.name] = call.params.get(arg.name, arg.default)
        elif arg.kind == "review_note":
            args[arg.name] = meta.review_note
        elif arg.kind == "previous":
            args[arg.name] = previous_output(call, step, item, meta, workdir / "previous")
        elif arg.kind == "step":
            args[arg.name], meta.inputs[arg.name] = step_input(
                call, arg, item, inputs_key, workdir / "inputs"
            )
        elif arg.kind == "items":
            args[arg.name], meta.inputs[arg.name] = items_input(call, arg, inputs_key, workdir / "inputs")
        elif item is not None and arg.name in item_inputs:
            args[arg.name], meta.inputs[arg.name] = item_input(
                call, arg.name, item, inputs_key, workdir / "inputs"
            )
        else:
            args[arg.name] = arg.default
    return args


def _item_info(call: Call, item: str | None) -> ItemInfo:
    return next(i for i in call.manifest.items if i.id == item)


def context(call: Call, step: Step, item: str | None, meta: StepMetadata, workdir: Path) -> Context:
    workdir.mkdir(parents=True, exist_ok=True)
    return Context(
        load_previous=lambda: previous_output(call, step, item, meta, workdir.parent / "previous"),
        run_id=call.manifest.run_id,
        trace_id=call.manifest.trace_id,
        item_id=item or GLOBAL_ITEM,
        step=step.name,
        attempt=meta.attempt,
        seed=meta.seed,
        logger=logging.getLogger(f"hone_flow.steps.{step.name}"),
        workdir=workdir,
        trace=step_trace(call, step, item, meta),
        gpu=call.wf.gpu,
        resources=step.resources,
    )


def previous_output(call: Call, step: Step, item: str | None, meta: StepMetadata, local: Path) -> Any:
    """The step's latest rejected or replaced output for this item (change 0006), loaded from its
    ``attempts/<n>/output/``; ``None`` when there is none. Several outputs come as a tuple."""
    earlier = next(
        (a for a in reversed(meta.attempts) if a.status in ("rejected", "replaced") and a.outputs), None
    )
    if earlier is None:
        return None
    prefix = call.folder.step_key(step.name, item, "attempts", str(earlier.attempt), "output")
    stored = [earlier.outputs[name] for name in step.outputs if name in earlier.outputs]
    values = [load_output(call.folder.storage, prefix, output, local) for output in stored]
    return values[0] if len(values) == 1 else tuple(values)


def step_input(call: Call, arg: Arg, item: str | None, inputs_key: str, local: Path) -> tuple[Any, InputInfo]:
    """Copy another step's output into ``inputs/<parameter><suffix>`` and load it for the step."""
    storage, folder = call.folder.storage, call.folder
    source = call.wf.graph().steps[arg.source]
    src_item = None if source.once else item
    produced = folder.read_meta(arg.source, src_item)
    if produced is None or arg.name not in produced.outputs:
        raise HoneFlowError(f"step {arg.source!r} has no output {arg.name!r} for item {item!r}")
    output = produced.outputs[arg.name]
    src_prefix = folder.step_key(arg.source, src_item, "output")
    files: dict[
        str, FileInfo
    ] = {}  # the snapshot's names: inputs/<parameter><suffix> or inputs/<parameter>/...
    for name, info in output.files.items():
        dst = (
            f"{arg.name}/{name.split('/', 1)[1]}"
            if output.type == "dir"
            else arg.name + PurePosixPath(name).suffix
        )
        storage.copy(f"{src_prefix}/{name}", f"{inputs_key}/{dst}")
        files[dst] = info
    value = load_output(storage, inputs_key, OutputInfo(type=output.type, files=files), local)
    return value, InputInfo(source=f"{source.kind if source.once else 'step'}:{arg.source}", files=files)


def items_input(call: Call, arg: Arg, inputs_key: str, local: Path) -> tuple[dict[str, Any], InputInfo]:
    """A fan-in parameter (design §4.18): an item step's output for every item where it is ``done``,
    by item id in manifest order, each snapshotted as ``inputs/<parameter>/<item id>/<file>``."""
    storage, folder = call.folder.storage, call.folder
    source = call.wf.graph().steps[arg.source]
    values: dict[str, Any] = {}
    files: dict[str, FileInfo] = {}
    for item in item_ids(call, source):
        produced = folder.read_meta(arg.source, item)
        if call.manifest.state.get(state_key(arg.source, item)) != "done" or produced is None:
            continue
        output = produced.outputs[arg.name]
        src_prefix = folder.step_key(arg.source, item, "output")
        for name in output.files:
            storage.copy(f"{src_prefix}/{name}", f"{inputs_key}/{arg.name}/{item}/{name}")
            files[f"{arg.name}/{item}/{name}"] = output.files[name]
        prefix = f"{inputs_key}/{arg.name}/{item}"
        values[item] = load_output(storage, prefix, output, local / arg.name / item)
    return values, InputInfo(source=f"items:{arg.source}", files=files)


def item_input(call: Call, name: str, item: str, inputs_key: str, local: Path) -> tuple[Any, InputInfo]:
    """Snapshot an item input: a file as ``inputs/<name><suffix>``, a folder, or a JSON value as
    ``inputs/<name>.json``. A file or folder must still have the content the manifest recorded."""
    storage = call.folder.storage
    record = _item_info(call, item).inputs[name]
    if record.kind == "json":
        data = canonical_json(record.value)
        storage.write_bytes(f"{inputs_key}/{name}.json", data)
        files = {f"{name}.json": FileInfo(sha256=sha256_bytes(data), size=len(data))}
        return record.value, InputInfo(source="item_value", files=files)
    path = Path(record.path or "")
    if not path.exists() and record.snapshot is None:
        raise HoneFlowError(
            f"input {name!r} of item {item!r}: {path} no longer exists; restore it, or fork with "
            "fk.Item objects that name the new path"
        )
    if record.kind == "dir":
        if path.exists():
            upload_tree(storage, path, f"{inputs_key}/{name}")
        else:  # a fork whose original is gone uses the source run's snapshot
            for key in storage.list(record.snapshot or ""):
                storage.copy(key, f"{inputs_key}/{name}{key.removeprefix(record.snapshot or '')}")
        download_tree(storage, f"{inputs_key}/{name}", local / name)
        value, files = Dir(local / name), dir_files(local / name, name)
        found = digest(dir_files(local / name))
    else:
        dst = name + path.suffix
        if path.exists():
            storage.upload(path, f"{inputs_key}/{dst}")
        else:
            storage.copy(record.snapshot or "", f"{inputs_key}/{dst}")
        storage.download(f"{inputs_key}/{dst}", local / dst)
        value, files = File(local / dst), {dst: file_info(local / dst)}
        found = files[dst].sha256
    if found != record.sha256:
        raise HoneFlowError(
            f"input {name!r} of item {item!r} ({path}) changed since the run started; resume never mixes "
            "inputs: restore it, or fork the run (the fork reruns what depends on it)"
        )
    return value, InputInfo(source="item_input", files=files)


def item_arg(call: Call, item: str, inputs_key: str, local: Path, meta: StepMetadata) -> Item:
    """``item: fk.Item``: the item with every input snapshotted, as the step receives it."""
    inputs: dict[str, Any] = {}
    for name in _item_info(call, item).inputs:
        inputs[name], meta.inputs[name] = item_input(call, name, item, inputs_key, local)
    return Item(item, inputs)
