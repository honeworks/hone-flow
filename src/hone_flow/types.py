"""Plain data types users see: items, files and folders, params and the step context."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypeAlias, TypeVar

from pydantic import BaseModel

from hone_flow.ports import GpuLease

T = TypeVar("T")

GLOBAL_ITEM = "_global"
"""Item id under which global steps (``@wf.global_step``) are recorded."""


class _ParamMarker:
    def __repr__(self) -> str:
        return "hone_flow.Param"


PARAM_MARKER = _ParamMarker()

Param: TypeAlias = Annotated[T, PARAM_MARKER]
"""Annotation marking a run-level parameter: ``def shotlist(style: fk.Param[str]) -> ...``.

The value comes from ``wf.run(items, params={"style": "noir"})``. Only the params a step declares
reach it.
"""


@dataclass(frozen=True, init=False)
class File:
    """A file: an item input or a step output.

    >>> File("songs/01.md").path.name
    '01.md'
    """

    path: Path

    def __init__(self, path: str | os.PathLike[str]) -> None:
        object.__setattr__(self, "path", Path(path))


@dataclass(frozen=True, init=False)
class Dir:
    """A folder: an item input or a step output."""

    path: Path

    def __init__(self, path: str | os.PathLike[str]) -> None:
        object.__setattr__(self, "path", Path(path))


@dataclass(frozen=True)
class Item:
    """One unit the workflow fans out over (a song, a document, ...).

    >>> Item("01", {"lyrics": File("songs/01.md"), "mood": "calm"}).id
    '01'
    """

    id: str
    inputs: Mapping[str, Any] = field(default_factory=dict[str, Any])


class Items(list[Item]):
    """The items a global step creates (design §4.20): return ``fk.Items([fk.Item(...), ...])`` and the
    steps declared ``@wf.step(per="<that step>")`` run once per produced item.

    >>> len(Items([Item("ch1", {"title": "Why"}), Item("ch2", {"title": "How"})]))
    2
    """


@dataclass
class Context:
    """Injected into a step that declares ``ctx: fk.Context``.

    ``item_id`` is ``"_global"`` in a global step. ``attempt`` counts retries and revisions (1-based).
    ``new_file`` / ``new_dir`` give paths in the attempt's local work folder; return them as
    ``fk.File`` / ``fk.Dir`` to store them as outputs.
    """

    run_id: str
    trace_id: str
    item_id: str
    step: str
    attempt: int
    seed: int
    logger: logging.Logger
    workdir: Path
    trace: dict[str, str]
    gpu: GpuLease
    resources: str = "cpu"
    run_label: tuple[str, str | None] | None = None  # set by set_run_label, applied on commit
    load_previous: Callable[[], Any] | None = None  # see previous_output

    def set_run_label(self, label: str, description: str | None = None) -> None:
        """Name the run (``label``, optional ``description``) once this step's result is committed.

        For names that are only known inside a step, such as a title the step wrote. A failed attempt
        changes nothing. ``description=None`` keeps the run's current description.
        """
        self.run_label = (label, description)

    def previous_output(self) -> Any:
        """This step's latest rejected (or replaced) output for this item, loaded like ``run.output``,
        or ``None`` when there is none (a first attempt, or a fork). Several outputs come as a tuple.

        The same value a step receives through a parameter named ``previous``: a revision can keep what
        passed review and change only what the note asks for.
        """
        return self.load_previous() if self.load_previous is not None else None

    def new_file(self, name: str) -> Path:
        """Return a fresh path in this attempt's work folder (parents created)."""
        path = self.workdir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def new_dir(self, name: str) -> Path:
        """Create and return a folder in this attempt's work folder."""
        path = self.workdir / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    def current_trace(self) -> dict[str, str]:
        """Trace context to pass on: ``client.complete(..., trace=ctx.current_trace())``."""
        return dict(self.trace)

    def gpu_lease(
        self, vram_gb: float = 0.0, *, name: str | None = None, timeout_s: float | None = None
    ) -> AbstractContextManager[None]:
        """Hold the workflow's GPU lease around part of a step.

        The default name is the step's ``gpu:`` tag (already held by its batch, so this re-enters it),
        or ``hone-flow:<step>`` for other steps.
        """
        default = self.resources if self.resources.startswith("gpu:") else f"hone-flow:{self.step}"
        return self.gpu.lease(name or default, vram_gb, timeout_s=timeout_s, trace=self.trace)


class Selection(BaseModel):
    """What a ``@wf.select_step`` decides: the item ids that continue (``keep``) and, optionally, why the
    others do not (``reasons``: item id -> text). A select step may also return just the list of ids.

    >>> Selection(keep=["03", "07"], reasons={"05": "same company as 03"}).keep
    ['03', '07']
    """

    keep: list[str]
    reasons: dict[str, str] = {}


@dataclass(frozen=True)
class StepRecord:
    """One step of a run for one item (``item=None``: a global step), as ``run.steps()`` returns it.

    From the step's ``metadata.json`` when it exists, else from the manifest's state (a ``pending``,
    ``skipped`` or ``blocked`` step has no folder). ``attempts``, ``reviews``, ``inputs``, ``outputs``,
    ``error`` and ``measurements`` are the plain JSON of ``docs/run-format.md``.
    """

    step: str
    item: str | None
    kind: str
    status: str
    version: str
    source_hash: str
    attempt: int
    attempts: list[dict[str, Any]]
    labels: list[str]
    reused_from: str | None
    reviews: list[dict[str, Any]]
    review_note: str | None
    params: dict[str, Any]
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    error: dict[str, Any] | None
    started_at: str | None
    ended_at: str | None
    duration_ms: int | None
    measurements: dict[str, Any]
    location: str


@dataclass(frozen=True)
class ForkPlanRow:
    """What a fork does with one step for one item (``item=None``: a global step).

    ``action`` is ``reuse`` (copy the source's result) or ``run``; ``reason`` says why, e.g.
    ``unchanged``, ``version_changed 1->2``, ``param_changed:style``, ``downstream_of:shotlist``.
    """

    item: str | None
    step: str
    action: str
    reason: str


@dataclass(frozen=True)
class ForkPlan:
    """The plan of ``run.fork(..., dry_run=True)``: one row per step and item of the new run."""

    source_run_id: str
    rows: tuple[ForkPlanRow, ...]


@dataclass(frozen=True)
class RunSummary:
    """One logical run in a listing (``wf.runs()``, ``fk.open_runs(...).runs()``).

    ``fork_of`` is the source run id of a fork; ``pinned`` is true when a verified copy exists under
    ``pinned_runs/``; ``items`` are the item ids; ``location`` is the run folder (``runs/`` when it
    still exists, else the pinned copy); ``label`` and ``description`` name the run (``None`` when unset);
    ``attempts`` maps ``"<step>/<item>"`` (or ``"<step>"``) to the attempt count of units tried more than
    once.
    """

    run_id: str
    workflow: str
    workflow_version: str
    status: str
    created_at: str
    updated_at: str
    fork_of: str | None
    pinned: bool
    items: tuple[str, ...]
    location: str
    label: str | None = None
    description: str | None = None
    attempts: Mapping[str, int] = field(default_factory=dict[str, int])


@dataclass(frozen=True)
class LeaseInfo:
    """Who holds a run (``run.lease()``): ``live`` is true while that process may still change it
    (same host: the pid is alive; another host: ``expires_at`` is in the future)."""

    host: str
    pid: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str
    live: bool


@dataclass(frozen=True)
class CleanupReport:
    """What ``cleanup`` deleted (or would delete with ``dry_run=True``), kept, and skipped as locked."""

    deleted: tuple[str, ...]
    kept: tuple[str, ...]
    locked: tuple[str, ...]


@dataclass(frozen=True)
class Delivery:
    """The result of sending one notification event to one destination (``run.retry_notifications()``)."""

    event_id: str
    event: str
    destination: str
    state: str  # delivered, failed
    attempts: int
    error: str | None
