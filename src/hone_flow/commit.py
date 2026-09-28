"""Committing one step attempt (design §4.4): input snapshots, the call, the outputs, then ``metadata.json``
last, which is the commit marker.

A step always sees local paths: its inputs are snapshotted into the run folder's ``inputs/`` and then
fetched into the attempt's local work folder; the files it returns are stored under ``output/``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hone_flow._records import strip_secrets
from hone_flow._tracing import iso_now, new_span_id, using_trace
from hone_flow.dag import Step
from hone_flow.errors import HoneFlowError
from hone_flow.inputs import prepare_inputs, step_trace
from hone_flow.invoke import as_selection, failure_text, split_outputs, step_seed
from hone_flow.metrics import SAMPLE_INTERVAL_S, Sampler, probes_for
from hone_flow.ports import RecordSink, RunStorage
from hone_flow.progress import report
from hone_flow.run_format import (
    Attempt,
    ErrorInfo,
    FileInfo,
    OutputInfo,
    RunFolder,
    StepMetadata,
    apply_label,
    dir_files,
    file_info,
    note_attempt,
    split_state_key,
    state_key,
)
from hone_flow.serialize import dump_value, sha256_bytes
from hone_flow.spans import SpanLog, state_span, unit_span
from hone_flow.storage import upload_tree
from hone_flow.types import GLOBAL_ITEM, Context, Dir, File, Items

if TYPE_CHECKING:
    from hone_flow.executor import Call


def run_attempt(call: Call, step: Step, item: str | None) -> StepMetadata:
    """Run one attempt of a step for one item (``None``: a global step), commit it and return its metadata."""
    folder = call.folder
    prev = folder.read_meta(step.name, item)
    earlier = earlier_attempts(prev)
    set_state(call, step.name, item, "running")
    meta = StepMetadata(
        run_id=call.manifest.run_id,
        step=step.name,
        item=item,
        kind=step.kind,
        status="running",
        version=step.version,
        source_hash=step.source_hash,
        deterministic=step.deterministic,
        resources=step.resources,
        params=strip_secrets(declared_params(call, step)),
        seed=seed_for(call, step, item, earlier),
        attempt=len(earlier) + 1,
        review_note=prev.review_note if prev else None,
        reviews=prev.reviews if prev else [],
        attempts=earlier,
        started_at=iso_now(),
        span_id=new_span_id(),
    )
    workdir = call.workdir / step.name / (item or GLOBAL_ITEM) / str(meta.attempt)
    report(call, "step_started", step=step.name, item=item, attempt=meta.attempt)
    started = time.monotonic()
    sampler = Sampler(SAMPLE_INTERVAL_S, probes_for(call.manifest.measure))
    args: dict[str, Any] = {}
    produced: Items | None = None
    try:
        args = prepare_inputs(call, step, item, meta, workdir)
        with sampler, using_trace(step_trace(call, step, item, meta)):
            result = step.fn(**args)
        if step.kind == "select":
            result = as_selection(step, result, [i.id for i in call.manifest.items])
        elif step.produces_items:
            from hone_flow.produced import check_produced  # noqa: PLC0415 - produced.py imports this module

            produced = check_produced(call, step, result)
        meta.outputs = write_outputs(
            folder.storage, folder.step_key(step.name, item, "output"), split_outputs(step, result)
        )
        meta.status = "awaiting_review" if step.kind == "gate" else "done"
    except Exception:
        record_failure(call, meta, workdir / "work")
    meta.ended_at = iso_now()
    meta.duration_ms = round((time.monotonic() - started) * 1000)
    meta.measurements = measurements(call, meta, sampler)
    call.lease_wait_ms = 0.0  # a batch's lease wait is charged to its first attempt only
    folder.write_meta(meta)  # the commit marker
    if meta.error is None:
        apply_step_label(call, args)
        if produced is not None:
            from hone_flow.produced import add_produced  # noqa: PLC0415 - produced.py imports this module

            add_produced(call, step, produced)
    note_attempt(call.manifest, meta)
    set_state(call, step.name, item, meta.status)
    system = meta.measurements.get("system", {})
    call.spans.emit(unit_span(call, step, item, meta.status, meta=meta, extra=system, events=sampler.events))
    call.spans.flush()
    report(
        call,
        "step_finished",
        step=step.name,
        item=item,
        attempt=meta.attempt,
        status=meta.status,
        duration_ms=meta.duration_ms,
    )
    return meta


def measurements(call: Call, meta: StepMetadata, sampler: Sampler) -> dict[str, Any]:
    """The enabled measurements of an attempt (design §4.10); the GPU lease wait is charged once."""
    found: dict[str, Any] = {}
    if "timing" in call.manifest.measure:
        found["timing"] = {"duration_ms": meta.duration_ms, "lease_wait_ms": round(call.lease_wait_ms)}
    if "output_sizes" in call.manifest.measure:
        found["output_sizes"] = {
            "total_bytes": sum(f.size for o in meta.outputs.values() for f in o.files.values())
        }
    if sampler.events:
        found["system"] = sampler.summary()
    return found


def apply_step_label(call: Call, args: dict[str, Any]) -> None:
    """A committed step that called ``ctx.set_run_label`` names the run (written with its state)."""
    ctx = next((v for v in args.values() if isinstance(v, Context)), None)
    if ctx is not None and ctx.run_label is not None:
        apply_label(call.manifest, *ctx.run_label)


def set_state(call: Call, step: str, item: str | None, state: str) -> None:
    call.manifest.state[state_key(step, item)] = state
    call.folder.write_manifest(call.manifest)


def earlier_attempts(prev: StepMetadata | None) -> list[Attempt]:
    """The ``attempts`` list of the next attempt: a failed latest attempt joins the earlier ones."""
    if prev is None:
        return []
    if prev.status != "failed":
        return list(prev.attempts)
    failed = Attempt(
        attempt=prev.attempt,
        status="failed",
        started_at=prev.started_at,
        ended_at=prev.ended_at,
        error=prev.error,
    )
    return [*prev.attempts, failed]


def declared_params(call: Call, step: Step) -> dict[str, Any]:
    return {a.name: call.params.get(a.name, a.default) for a in step.args if a.kind == "param"}


def seed_for(call: Call, step: Step, item: str | None, earlier: list[Attempt]) -> int:
    """``ctx.seed``: stable across resume and retries; a rejection or a fork's refresh gives a new sample."""
    salt = ""
    rejections = sum(1 for a in earlier if a.status == "rejected")
    if rejections:
        salt += f":revision{rejections}"
    fork = call.manifest.fork_of
    if fork is not None and any(
        r.step == step.name and r.item == item and "refresh_requested" in r.reason.split("; ")
        for r in fork.plan
    ):
        salt += f":{call.manifest.run_id}"
    return step_seed(call.manifest.seed, item or GLOBAL_ITEM, step.name, salt)


def write_outputs(storage: RunStorage, prefix: str, outputs: dict[str, Any]) -> dict[str, OutputInfo]:
    """Store a step's outputs under ``output/`` with readable names; return what was stored."""
    stored: dict[str, OutputInfo] = {}
    for name, value in outputs.items():
        if isinstance(value, File | Dir) and not value.path.exists():
            raise HoneFlowError(f"output {name!r} is {value!r}, which does not exist")
        if isinstance(value, File):
            files = {value.path.name: file_info(value.path)}
            storage.upload(value.path, f"{prefix}/{value.path.name}")
            stored[name] = OutputInfo(type="file", files=files)
        elif isinstance(value, Dir):
            files = dir_files(value.path, value.path.name)
            if not files:  # an empty folder has no object on S3, so it cannot be stored
                raise HoneFlowError(f"output {name!r} is an empty folder ({value.path}); put a file in it")
            upload_tree(storage, value.path, f"{prefix}/{value.path.name}")
            stored[name] = OutputInfo(type="dir", files=files)
        else:
            data, type_, extension = dump_value(value)
            files = {f"{name}.{extension}": FileInfo(sha256=sha256_bytes(data), size=len(data))}
            storage.write_bytes(f"{prefix}/{name}.{extension}", data)
            stored[name] = OutputInfo(type=type_, files=files)
    taken = [f for info in stored.values() for f in info.files]
    if len(taken) != len(set(taken)):
        raise HoneFlowError(f"outputs {sorted(outputs)} would share file names {sorted(taken)}; rename one")
    return stored


def record_failure(call: Call, meta: StepMetadata, work: Path) -> None:
    """A failed attempt: traceback in metadata, whatever it wrote under ``attempts/<n>/output/``."""
    traceback = failure_text()
    meta.status = "failed"
    meta.error = ErrorInfo(message=traceback.strip().splitlines()[-1], traceback=traceback)
    meta.outputs = {}
    folder = call.folder
    folder.storage.delete(folder.step_key(meta.step, meta.item, "output"))
    if work.exists():
        upload_tree(
            folder.storage,
            work,
            folder.step_key(meta.step, meta.item, "attempts", str(meta.attempt), "output"),
        )


def recover(folder: RunFolder, sink: RecordSink | None = None) -> None:
    """Repair a run after a crash, under its lease (so nothing can be running now).

    A ``running`` step without a committed result becomes ``interrupted`` (with a span for the lost
    attempt), and a ``running`` run ``interrupted``. Every step folder is made exactly what its
    ``metadata.json`` says: a folder without it is deleted; attempt folders, ``output/`` and input
    snapshots it does not list are deleted. Otherwise, where the manifest disagrees, metadata wins.
    """
    manifest = folder.read_manifest()
    spans = SpanLog(folder.storage, folder.key("spans.jsonl"), sink)
    for key, state in manifest.state.items():
        step, item = split_state_key(key)
        meta = folder.read_meta(step, item)
        if meta is None:
            folder.storage.delete(folder.step_key(step, item))
        else:
            _clean_unit(folder.storage, folder.step_key(step, item), meta)
        committed = meta is not None and meta.status in ("done", "awaiting_review")
        if state == "running" and not committed:
            manifest.state[key] = "interrupted"
            last_call = manifest.calls[-1].span_id if manifest.calls else None  # the call that died
            spans.emit(state_span(manifest, step, item, "interrupted", parent=last_call))
        elif meta is not None:
            manifest.state[key] = meta.status
        if meta is not None:
            note_attempt(manifest, meta)
    if manifest.status == "running":
        manifest.status = "interrupted"
    folder.write_manifest(manifest)
    spans.flush()


def _clean_unit(storage: RunStorage, unit: str, meta: StepMetadata) -> None:
    listed: set[int] = {a.attempt for a in meta.attempts}
    if meta.status == "failed":
        listed.add(meta.attempt)
    for key in storage.list(f"{unit}/attempts"):
        number = key.removeprefix(f"{unit}/attempts/").split("/")[0]
        if not number.isdigit() or int(number) not in listed:
            storage.delete(f"{unit}/attempts/{number}")
    if not meta.outputs:
        storage.delete(f"{unit}/output")
    snapshots = {name for info in meta.inputs.values() for name in info.files}
    for key in storage.list(f"{unit}/inputs"):  # e.g. a retry that crashed while snapshotting
        if key.removeprefix(f"{unit}/inputs/") not in snapshots:
            storage.delete(key)


def retire(folder: RunFolder, meta: StepMetadata, status: str, note: str | None = None) -> None:
    """Move a step's current result to ``attempts/<n>/output/`` and leave it ``pending`` (design §4.4).

    Copy first, then write the metadata that lists the attempt, then delete ``output/``: a crash at any
    point leaves a folder that ``recover`` can make consistent.
    """
    unit = folder.step_key(meta.step, meta.item)
    for key in folder.storage.list(f"{unit}/output"):
        folder.storage.copy(
            key, f"{unit}/attempts/{meta.attempt}/output/{key.removeprefix(f'{unit}/output/')}"
        )
    meta.attempts.append(
        Attempt(
            attempt=meta.attempt,
            status=status,
            started_at=meta.started_at,
            ended_at=meta.ended_at,
            note=note,
            outputs=meta.outputs,
        )
    )
    meta.status, meta.outputs, meta.labels, meta.error = "pending", {}, [], None
    folder.write_meta(meta)
    folder.storage.delete(f"{unit}/output")
