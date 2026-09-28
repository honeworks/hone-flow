"""Calling one step: its stable seed, splitting its return value into outputs, its failure text."""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Sequence
from typing import Any, cast

from hone_flow._records import strip_secrets
from hone_flow.dag import Step
from hone_flow.errors import HoneFlowError
from hone_flow.types import Selection


def step_seed(run_seed: int, item_id: str, step: str, salt: str = "") -> int:
    """``ctx.seed``: ``int(sha256(run seed, item, step)[:8], 16)``, stable across resume and retries.

    ``salt`` (a new run id or attempt number) gives a refreshed or rejected step a new sample.

    >>> step_seed(0, "01", "shotlist") == step_seed(0, "01", "shotlist")
    True
    """
    digest = hashlib.sha256(f"{run_seed}:{item_id}:{step}{salt}".encode()).hexdigest()
    return int(digest[:8], 16)


def split_outputs(step: Step, result: Any) -> dict[str, Any]:
    """A step's return value by output name: one output, or one per name in ``outputs=`` (a tuple)."""
    if len(step.outputs) == 1:
        return {step.outputs[0]: result}
    values = cast(Sequence[Any], result) if isinstance(result, tuple | list) else ()
    if len(values) != len(step.outputs):
        raise HoneFlowError(
            f"step {step.name!r} declares outputs {step.outputs}; return a tuple of that length"
        )
    return dict(zip(step.outputs, values, strict=True))


def as_selection(step: Step, result: Any, item_ids: Sequence[str]) -> Selection:
    """A select step's return value as a ``Selection``; ids must be items of the run."""
    ids = cast(Sequence[Any], result) if isinstance(result, list | tuple) else None
    if isinstance(result, Selection):
        chosen = result
    elif ids is not None and all(isinstance(i, str) for i in ids):
        chosen = Selection(keep=[str(i) for i in ids])
    else:
        raise HoneFlowError(
            f"select step {step.name!r} must return a list of item ids or fk.Selection, "
            f"not {type(cast(object, result)).__name__}"
        )
    if unknown := sorted(set(chosen.keep) - set(item_ids)):
        raise HoneFlowError(
            f"select step {step.name!r} kept unknown items {unknown}; items: {list(item_ids)}"
        )
    return chosen


def failure_text() -> str:
    """The traceback of the exception being handled, with secrets replaced by ``***``."""
    return str(strip_secrets(traceback.format_exc()))
