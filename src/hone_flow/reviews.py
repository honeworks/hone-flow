"""Human decisions on gates (design §4.8): approve, edit, reject-and-revise.

A gate reviews the outputs of the steps it takes as input (its *producers*). Decisions work from the
run folder alone (no workflow code needed): the manifest's step table says which steps feed which.
"""

from __future__ import annotations

import getpass
import os
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from hone_flow._records import strip_secrets
from hone_flow._tracing import iso_now
from hone_flow.commit import recover, retire, write_outputs
from hone_flow.dag import RUN_LEVEL
from hone_flow.errors import HoneFlowError, ReviewError
from hone_flow.run_format import Manifest, Review, StepInfo, StepMetadata, note_attempt, state_key
from hone_flow.run_lease import RunLease
from hone_flow.serialize import dump_value, import_type
from hone_flow.spans import SpanLog, state_span
from hone_flow.types import Dir, File

if TYPE_CHECKING:
    from hone_flow.run import Run


def approve(
    run: Run,
    step: str,
    item: str | None,
    *,
    note: str = "",
    actor: str | None = None,
    automated: bool = False,
) -> None:
    """The gate's output is accepted as it is (label ``approved``); ``resume()`` continues downstream."""
    with _gate_under_lease(run, step, item) as (manifest, gate):
        gate.labels = ["approved"]
        who = Who(actor, automated)
        _decide(run, manifest, gate, "approved", note=note, who=who, attempt=gate.attempt)


def edit(
    run: Run, step: str, item: str | None, *, value: Any, actor: str | None = None, automated: bool = False
) -> None:
    """Replace the gate's output with ``value`` (label ``edited``); the old output moves to ``attempts/``."""
    with _gate_under_lease(run, step, item) as (manifest, gate):
        reviewed = gate.attempt
        ((name, output),) = gate.outputs.items()
        value = _storable(_as_gate_type(value, output.type))  # check before anything moves
        retire(run.folder, gate, "replaced")
        gate.outputs = write_outputs(
            run.folder.storage, run.folder.step_key(step, item, "output"), {name: value}
        )
        gate.attempt, gate.started_at, gate.ended_at = reviewed + 1, iso_now(), iso_now()
        gate.duration_ms, gate.measurements, gate.span_id = 0, {}, None
        gate.labels = ["edited"]
        _decide(run, manifest, gate, "edited", note="", who=Who(actor, automated), attempt=reviewed)


def reject(
    run: Run, step: str, item: str | None, *, note: str, actor: str | None = None, automated: bool = False
) -> None:
    """Send the gate's producers back with ``note``: their outputs (and everything downstream of them)
    move to ``attempts/``; ``resume()`` reruns them, passing the note as ``review_note``."""
    note = str(strip_secrets(note))  # it is stored and passed on as review_note
    with _gate_under_lease(run, step, item) as (manifest, gate):
        retire(run.folder, gate, "rejected", note)  # first: a crash below never lets stale output pass
        producers = _producers(manifest, step)
        for info in producers:
            _retire_unit(
                run,
                manifest,
                info.name,
                None if info.kind in RUN_LEVEL else item,
                status="rejected",
                note=note,
            )
        for name, other in _replaced_units(manifest, producers, item):  # retired producers are skipped
            _retire_unit(run, manifest, name, other, status="replaced")  # the retired gate is skipped too
            if manifest.state.get(state_key(name, other)) == "not_selected":
                manifest.state[state_key(name, other)] = "pending"  # a select step decides again
        _decide(run, manifest, gate, "rejected", note=note, who=Who(actor, automated), attempt=gate.attempt)


@dataclass(frozen=True)
class Who:
    """Who decided: ``actor`` (default ``$USER``) and whether it was a program (a script, a model)."""

    actor: str | None
    automated: bool = False


@contextmanager
def _gate_under_lease(run: Run, step: str, item: str | None) -> Generator[tuple[Manifest, StepMetadata]]:
    """Hold the run lease and yield the manifest and the gate's metadata, if the gate awaits review."""
    run.require_writable("a review decision")
    with RunLease(run.folder.storage, run.folder.prefix) as lease:
        if lease.taken_over:
            recover(run.folder, run.sink)
        manifest = run.folder.read_manifest()
        info = next((s for s in manifest.steps if s.name == step), None)
        if info is None or info.kind != "gate":
            gates = [s.name for s in manifest.steps if s.kind == "gate"]
            raise ReviewError(f"{step!r} is not a gate of run {run.run_id}; gates: {gates}")
        if item is None:
            raise ReviewError(f"gate {step!r} reviews each item: pass item=")
        meta = run.folder.read_meta(step, item)
        if meta is None or meta.status != "awaiting_review":
            state = meta.status if meta else manifest.state.get(state_key(step, item), "pending")
            raise ReviewError(f"gate {step!r} is not awaiting review for item {item!r} (it is {state})")
        yield manifest, meta


def _decide(
    run: Run,
    manifest: Manifest,
    gate: StepMetadata,
    decision: str,
    *,
    note: str,
    who: Who,
    attempt: int,
) -> None:
    """Record the decision in the gate's metadata, the manifest state and a ``hone.flow.gate`` span."""
    actor = who.actor or os.environ.get("USER") or getpass.getuser()
    kind = "automated" if who.automated else "person"
    note = str(strip_secrets(note))
    gate.reviews.append(
        Review(decision=decision, actor=actor, actor_kind=kind, note=note, at=iso_now(), attempt=attempt)
    )
    if decision != "rejected":
        gate.status = "done"
    run.folder.write_meta(gate)
    manifest.state[state_key(gate.step, gate.item)] = gate.status
    note_attempt(manifest, gate)
    run.folder.write_manifest(manifest)
    extra = {
        "hone.flow.gate.decision": decision,
        "hone.flow.gate.actor": actor,
        "hone.flow.gate.actor_kind": kind,
        "hone.flow.gate.note": note,
        "hone.flow.attempt": attempt,
    }
    spans = SpanLog(run.folder.storage, run.folder.key("spans.jsonl"), run.sink)
    spans.emit(state_span(manifest, gate.step, gate.item, gate.status, extra=extra, parent=None))
    spans.flush()


def _retire_unit(
    run: Run, manifest: Manifest, step: str, item: str | None, *, status: str, note: str | None = None
) -> None:
    """Retire a step's current output (``rejected`` with the note for a producer, else ``replaced``)."""
    meta = run.folder.read_meta(step, item)
    if meta is None or not meta.outputs:
        return
    if status == "rejected":
        meta.review_note = note  # the next attempt receives it as `review_note`
    retire(run.folder, meta, status, note)
    manifest.state[state_key(step, item)] = "pending"


def _replaced_units(
    manifest: Manifest, producers: list[StepInfo], item: str | None
) -> list[tuple[str, str | None]]:
    """The units downstream of the rejected producers: an item step for this item only, unless it is
    downstream of a run-level step (a global producer, a final or select step): then for every item."""
    downstream = _downstream(manifest, [p.name for p in producers])
    run_level = [s.name for s in manifest.steps if s.name in downstream and s.kind in RUN_LEVEL]
    everywhere = _downstream(manifest, run_level)
    units: list[tuple[str, str | None]] = []
    for s in manifest.steps:
        group = [i.id for i in manifest.items if i.items_from == s.per]
        if s.name in downstream:
            others = [None] if s.kind in RUN_LEVEL else group if s.name in everywhere else [item]
            units += [(s.name, other) for other in others if other is None or other in group]
    return units


def _producers(manifest: Manifest, gate: str) -> list[StepInfo]:
    inputs = next(s for s in manifest.steps if s.name == gate).inputs
    return [s for s in manifest.steps if set(s.outputs) & set(inputs)]


def _downstream(manifest: Manifest, names: list[str]) -> set[str]:
    """The named steps and every step fed by them, from the manifest's step table (topological order)."""
    found = set(names)
    outputs = {s.name: set(s.outputs) for s in manifest.steps}
    for info in manifest.steps:
        if any(set(info.inputs) & outputs[name] for name in found):
            found.add(info.name)
    return found


def _as_gate_type(value: Any, output_type: str) -> Any:
    """Validate an edited value against the gate's Pydantic output type (when it can be imported)."""
    kind, _, path = output_type.partition(":")
    if kind != "pydantic":
        return value
    try:
        model = import_type(path)
    except (ImportError, AttributeError):
        return value
    if isinstance(value, model):
        return value
    try:
        return model.model_validate(value.model_dump() if isinstance(value, BaseModel) else value)
    except Exception as exc:
        raise ReviewError(f"the edited value is not a valid {path}: {exc}") from None


def _storable(value: Any) -> Any:
    """Refuse a value that cannot be stored as an output, before the old output is retired."""
    if isinstance(value, File | Dir):
        if not value.path.exists():
            raise ReviewError(f"the edited value {value!r} does not exist")
        return value
    try:
        dump_value(value)
    except HoneFlowError as exc:
        raise ReviewError(str(exc)) from None
    return value
