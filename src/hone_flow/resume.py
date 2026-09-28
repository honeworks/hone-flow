"""What resume checks before it runs anything (design §4.5, §4.19): it never mixes step versions, and it
does not run items a select step left out."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from hone_flow._tracing import iso_now
from hone_flow.errors import HoneFlowError, IncompatibleRun
from hone_flow.run_format import Manifest, split_state_key
from hone_flow.schedule import NEEDS_WORK

if TYPE_CHECKING:
    from hone_flow.workflow import Workflow

logger = logging.getLogger("hone_flow")


def refuse_not_selected(manifest: Manifest, items: Sequence[str] | None) -> None:
    """``resume(items=...)`` cannot run items a select step left out (design §4.19)."""
    wanted = set(items or ())
    left_out = sorted(
        {
            item
            for key, state in manifest.state.items()
            if state == "not_selected" and (item := split_state_key(key)[1]) is not None and item in wanted
        }
    )
    if left_out:
        raise HoneFlowError(
            f"items {left_out} were not selected by a select step of run {manifest.run_id}; to run them, "
            "fork with those items (or refresh the select step)"
        )


def check_resumable(wf: Workflow, manifest: Manifest) -> list[dict[str, Any]]:
    """Raise ``IncompatibleRun`` if resuming would mix versions; return source-change warnings.

    Incompatible: another workflow version, other steps or step inputs, or a new version of a step that
    still has work to do. A step with work whose source changed but whose version did not is a warning.
    """
    problems: list[str] = []
    if wf.version != manifest.workflow_version:
        problems.append(f"workflow version {manifest.workflow_version} -> {wf.version}")
    recorded = {s.name: s for s in manifest.steps}
    current = {s.name: s for s in wf.step_table()}
    if set(recorded) != set(current):
        added, removed = sorted(set(current) - set(recorded)), sorted(set(recorded) - set(current))
        problems.append(f"steps changed (added {added}, removed {removed})")
    warnings: list[dict[str, Any]] = []
    for name in sorted(set(recorded) & set(current)):
        old, new = recorded[name], current[name]
        if (old.kind, old.inputs, old.outputs, old.per) != (new.kind, new.inputs, new.outputs, new.per):
            problems.append(f"step {name!r} changed its inputs or outputs")
        elif _has_work(manifest, name) and old.version != new.version:
            problems.append(
                f"step {name!r} version {old.version} -> {new.version} and it still has work to do"
            )
        elif _has_work(manifest, name) and old.source_hash and new.source_hash != old.source_hash:
            warnings.append(
                {
                    "kind": "source_changed_version_unchanged",
                    "step": name,
                    "old": old.source_hash,
                    "new": new.source_hash,
                    "at": iso_now(),
                }
            )
    if problems:
        raise IncompatibleRun(
            f"cannot resume run {manifest.run_id}: "
            + "; ".join(problems)
            + ". Resume never mixes step versions: fork the run instead: run.fork()"
        )
    for warning in warnings:
        logger.warning("step %r changed its source without a version bump; resuming anyway", warning["step"])
    return warnings


def _has_work(manifest: Manifest, step: str) -> bool:
    return any(
        state in (*NEEDS_WORK, "running")
        for key, state in manifest.state.items()
        if split_state_key(key)[0] == step
    )
