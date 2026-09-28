"""``Workflow``: the single configuration point, step decorators, validation, and starting runs."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, TypeVar

from hone_flow._records import strip_secrets
from hone_flow._tracing import incoming_trace, iso_now, parent_of
from hone_flow.dag import Graph, Step, build_graph, check_items, make_step
from hone_flow.errors import WorkflowDefinitionError
from hone_flow.executor import execute, open_call
from hone_flow.history import RunHistory
from hone_flow.leases import NullGpuLease
from hone_flow.metrics import check_measure
from hone_flow.notifications import Webhook
from hone_flow.ports import GpuLease, RecordSink, RunStorage
from hone_flow.progress import OnEvent
from hone_flow.run import Run
from hone_flow.run_format import (
    ItemInfo,
    Manifest,
    RunFolder,
    StepInfo,
    apply_label,
    item_info,
    new_run_id,
    state_key,
)
from hone_flow.run_lease import RunLease
from hone_flow.serialize import canonical_json
from hone_flow.storage import open_storage
from hone_flow.types import CleanupReport, Item, RunSummary

F = TypeVar("F", bound=Callable[..., Any])

SAFE_NAME = re.compile(r"[A-Za-z0-9_.-]+")


def check_name(name: str) -> None:
    """A workflow name names a folder: letters, digits, ``_``, ``.`` and ``-`` only (not ``.`` / ``..``)."""
    if not SAFE_NAME.fullmatch(name) or name in (".", ".."):
        raise WorkflowDefinitionError(
            f"workflow name {name!r} is not a safe folder name: use letters, digits, '_', '.' and '-'"
        )


class Workflow:
    """A named, versioned set of steps whose wiring is inferred from parameter names.

    >>> import tempfile
    >>> wf = Workflow("demo", storage=tempfile.mkdtemp())
    >>> @wf.step()
    ... def double(x: int) -> int:
    ...     return 2 * x
    >>> wf.run([Item("a", {"x": 1})]).output("double", "a")
    2
    """

    def __init__(
        self,
        name: str,
        *,
        storage: str | os.PathLike[str] | RunStorage,
        version: str = "1",
        measure: Sequence[str] = ("timing", "output_sizes"),
        notifications: Sequence[Webhook] = (),
        order: str = "breadth_first",
        fail_fast: bool = False,
        gpu: GpuLease | str | None = None,
        sink: RecordSink | None = None,
    ) -> None:
        check_name(name)
        if order not in ("breadth_first", "depth_first"):
            raise WorkflowDefinitionError(f"order must be 'breadth_first' or 'depth_first', not {order!r}")
        self.name = name
        self.storage = open_storage(storage)
        self.version = str(version)
        self.measure = check_measure(measure)
        self.notifications = tuple(notifications)
        names = [d.name for d in self.notifications]
        if len(set(names)) != len(names):
            raise WorkflowDefinitionError(f"notification destinations need unique names; got {names}")
        self.order = order
        self.fail_fast = fail_fast
        self.sink = sink
        if isinstance(gpu, str):  # an entry point name in the group hone.gpu_leases, e.g. "hone_models"
            from hone_flow.adapters.hone_models import load_gpu_lease  # noqa: PLC0415 - adapters load lazily

            gpu = load_gpu_lease(gpu)
        self.gpu: GpuLease = gpu or NullGpuLease()
        self._steps: dict[str, Step] = {}
        self._graph: Graph | None = None

    def _add(self, step: Step) -> None:
        if step.name in self._steps:
            raise WorkflowDefinitionError(f"workflow {self.name!r} already has a step named {step.name!r}")
        taken = {out for s in self._steps.values() for out in s.outputs} | set(self._steps)
        clash = [out for out in step.outputs if out in taken]
        if clash:
            raise WorkflowDefinitionError(f"step {step.name!r} output names {clash} are already used")
        self._steps[step.name] = step
        self._graph = None

    def _register(self, kind: str, **options: Any) -> Callable[[F], F]:
        def register(fn: F) -> F:
            self._add(make_step(fn, kind, **options))
            return fn

        return register

    def step(
        self,
        version: str = "1",
        *,
        resources: str = "cpu",
        deterministic: bool = True,
        outputs: Sequence[str] | None = None,
        vram_gb: float = 0.0,
        per: str | None = None,
    ) -> Callable[[F], F]:
        """Register a per-item step. Its parameters name where their values come from.

        ``resources`` tags starting with ``gpu:`` hold the GPU lease around the step's batch.
        ``outputs=("a", "b")`` makes the step return a tuple of two separate outputs that other steps
        can depend on by name. ``per="<global step>"`` runs it once per item that global step produced
        (it returns ``fk.Items``) instead of once per item of the run.
        """
        return self._register(
            "step",
            version=version,
            resources=resources,
            deterministic=deterministic,
            outputs=outputs,
            vram_gb=vram_gb,
            per=per,
        )

    def gate(self, version: str = "1", *, per: str | None = None) -> Callable[[F], F]:
        """Register a human checkpoint that reviews the step outputs it takes as input
        (``per=`` as for ``step``)."""
        return self._register("gate", version=version, per=per)

    def global_step(
        self,
        version: str = "1",
        *,
        resources: str = "cpu",
        deterministic: bool = True,
        outputs: Sequence[str] | None = None,
        vram_gb: float = 0.0,
    ) -> Callable[[F], F]:
        """Register a step that runs once per run (not per item); item steps may depend on it."""
        return self._register(
            "global",
            version=version,
            resources=resources,
            deterministic=deterministic,
            outputs=outputs,
            vram_gb=vram_gb,
        )

    def final_step(
        self,
        version: str = "1",
        *,
        resources: str = "cpu",
        deterministic: bool = True,
        outputs: Sequence[str] | None = None,
        vram_gb: float = 0.0,
        partial_ok: bool = False,
    ) -> Callable[[F], F]:
        """Register a step that runs once after all items (fan-in; design §4.18).

        A parameter named after an item step (or one of its outputs) receives ``dict[item_id, output]``
        of the items where that step is ``done``; global and final steps' outputs arrive as they are.
        Unless ``partial_ok``, it waits until every item it needs is done (``blocked`` if one failed).
        """
        return self._register(
            "final",
            version=version,
            resources=resources,
            deterministic=deterministic,
            outputs=outputs,
            vram_gb=vram_gb,
            partial_ok=partial_ok,
        )

    def select_step(
        self,
        version: str = "1",
        *,
        resources: str = "cpu",
        deterministic: bool = True,
        vram_gb: float = 0.0,
        partial_ok: bool = False,
    ) -> Callable[[F], F]:
        """Register a step that chooses which items continue (design §4.19).

        It sees every item's output like a final step (``dict[item_id, output]``) and returns the ids that
        go on (a list, or ``fk.Selection(keep=..., reasons=...)``). Item steps that take its output run
        only for those items; for the others they become ``not_selected``.
        """
        return self._register(
            "select",
            version=version,
            resources=resources,
            deterministic=deterministic,
            vram_gb=vram_gb,
            partial_ok=partial_ok,
        )

    def graph(self) -> Graph:
        """The validated step graph (built once, rebuilt after new steps are added)."""
        if self._graph is None:
            self._graph = build_graph(self._steps)
        return self._graph

    def validate(self, items: Sequence[Item] | None = None) -> None:
        """Check the wiring; with ``items``, also check that every item input a step needs is present.

        Raises ``WorkflowDefinitionError`` with the step and parameter at fault.
        """
        graph = self.graph()
        if items is not None:
            check_items(graph, items)

    def run(
        self,
        items: Sequence[Item],
        *,
        params: Mapping[str, Any] | None = None,
        until: str | None = None,
        items_filter: Sequence[str] | None = None,
        seed: int = 0,
        trace: Mapping[str, str] | None = None,
        label: str | None = None,
        description: str | None = None,
        on_event: OnEvent | None = None,
    ) -> Run:
        """Start a new run folder and execute it; stops at gates and after failures (see ``Run.status``).

        ``until`` runs only that step and what it needs; ``items_filter`` runs only those item ids.
        Work left out is ``skipped`` (run status ``partial``) and ``resume()`` can do it later.
        ``label`` / ``description`` name the run for people (listings, notifications); see
        ``Run.set_label`` and ``Context.set_run_label`` to name it later. ``on_event`` receives progress
        events (``run_started`` with the run id and location first; see ``hone_flow.progress``).
        """
        items = list(items)
        params = dict(params or {})
        self.check_call(items, params, until, selected=items_filter)
        run_id = new_run_id()
        folder = RunFolder(self.storage, f"{self.name}/runs/{run_id}")
        incoming = incoming_trace(trace)
        records = [item_info(i) for i in items]
        manifest = self.new_manifest(folder, records, params, seed, trace_id=parent_of(incoming)[0])
        apply_label(manifest, label, description)
        with RunLease(self.storage, folder.prefix):
            folder.write_manifest(manifest, create=True)
            execute(
                open_call(
                    self,
                    folder,
                    kind="run",
                    params=params,
                    until=until,
                    items=items_filter,
                    trace=incoming,
                    on_event=on_event,
                )
            )
        return Run(self.storage, folder.prefix, self)

    def check_call(
        self,
        items: Sequence[Item],
        params: Mapping[str, Any],
        until: str | None,
        *,
        selected: Sequence[str] | None = None,
        produced: Sequence[str] = (),
    ) -> None:
        """Definition errors a call would hit: wiring, items, ``until``, selected items, params.

        ``items`` are the run's own items; ``produced`` the ids of items its steps made (design §4.20).
        """
        self.validate(items)
        known = [i.id for i in items] + list(produced)
        unknown = sorted(set(selected or ()) - set(known))
        if unknown:
            raise WorkflowDefinitionError(f"unknown items {unknown}; the run has {known}")
        graph = self.graph()
        if until is not None and until not in graph.steps:
            raise WorkflowDefinitionError(f"until={until!r} is not a step; steps: {list(graph.steps)}")
        try:
            canonical_json(dict(params))
        except (TypeError, ValueError) as exc:
            raise WorkflowDefinitionError(f"params must be JSON-compatible: {exc}") from None
        for step in graph.steps.values():
            for arg in step.args:
                if arg.kind == "param" and not arg.has_default and arg.name not in params:
                    raise WorkflowDefinitionError(
                        f"step {step.name!r} needs the run param {arg.name!r}; "
                        f"pass params={{{arg.name!r}: ...}}"
                    )

    def step_table(self) -> list[StepInfo]:
        """The manifest's ``steps``: what resume and fork compare the workflow with."""
        return [
            StepInfo(
                name=s.name,
                kind=s.kind,
                version=s.version,
                source_hash=s.source_hash,
                resources=s.resources,
                deterministic=s.deterministic,
                inputs=[a.name for a in s.args if a.kind in ("step", "items", "input")],
                outputs=list(s.outputs),
                per=s.per,
            )
            for s in self.graph().steps.values()
        ]

    def new_manifest(
        self, folder: RunFolder, items: list[ItemInfo], params: Mapping[str, Any], seed: int, *, trace_id: str
    ) -> Manifest:
        """The manifest of a new run: every step of every item ``pending``."""
        from hone_flow import __version__  # noqa: PLC0415 - the package imports this module first

        now = iso_now()
        graph = self.graph()
        state = {
            state_key(step.name, item): "pending"
            for step in graph.steps.values()
            for item in ([None] if step.once else [i.id for i in items if i.items_from == step.per])
        }
        return Manifest(
            run_id=folder.prefix.rsplit("/", 1)[-1],
            workflow=self.name,
            workflow_version=self.version,
            storage=self.storage.url,
            location=folder.location,
            hone_flow_version=__version__,
            created_at=now,
            updated_at=now,
            status="running",
            seed=seed,
            trace_id=trace_id,
            params=strip_secrets(dict(params)),
            items=items,
            steps=self.step_table(),
            state=state,
            measure=list(self.measure),
        )

    def _history(self) -> RunHistory:
        return RunHistory(self.storage, self.name, self)

    def open_run(self, run_id: str) -> Run:
        """The run with this id: ``runs/<id>``, else its pinned copy; ``RunNotFound`` if neither exists."""
        return self._history().open_run(run_id)

    def runs(self, *, updated_since: str | datetime | None = None) -> list[RunSummary]:
        """Every run of this workflow, newest first (a pinned run and its original are one run)."""
        return self._history().runs(updated_since=updated_since)

    def cleanup(
        self, *, keep_last: int | None = None, older_than: timedelta | None = None, dry_run: bool = False
    ) -> CleanupReport:
        """Delete old run folders under ``runs/`` (never pinned copies or runs in use); see ``RunHistory``."""
        return self._history().cleanup(keep_last=keep_last, older_than=older_than, dry_run=dry_run)
