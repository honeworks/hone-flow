"""``Run``: one run folder, read fresh on every access, and the operations on it."""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from hone_flow import fork, notifications, reviews
from hone_flow.commit import recover
from hone_flow.dag import RUN_LEVEL
from hone_flow.errors import HoneFlowError, OutputNotFound
from hone_flow.executor import execute, open_call
from hone_flow.ports import RecordSink, RunStorage
from hone_flow.progress import OnEvent
from hone_flow.resume import check_resumable, refuse_not_selected
from hone_flow.run_format import (
    Manifest,
    RunFolder,
    StepInfo,
    StepMetadata,
    apply_label,
    dump,
    item_from_info,
    load_output,
    state_key,
)
from hone_flow.run_lease import RunLease, is_live, read_lease
from hone_flow.storage import LocalStorage
from hone_flow.types import Delivery, Dir, File, ForkPlan, Item, LeaseInfo, StepRecord

if TYPE_CHECKING:
    from hone_flow.workflow import Workflow

logger = logging.getLogger("hone_flow")


class Run:
    """A run folder. ``status``, ``manifest`` and ``steps()`` read the files on every access.

    Attached runs (from ``wf.run`` / ``wf.open_run``) can be resumed and forked; detached runs (from
    ``fk.open_runs``) can be read, reviewed, pinned and can retry notifications.
    """

    def __init__(self, storage: RunStorage, prefix: str, workflow: Workflow | None = None) -> None:
        self.folder = RunFolder(storage, prefix)
        self.run_id = prefix.rsplit("/", 1)[-1]
        self.workflow = prefix.split("/", 1)[0]
        self.location = self.folder.location
        self._wf = workflow

    def __repr__(self) -> str:
        return f"Run({self.location!r})"

    def read_manifest(self) -> Manifest:
        return self.folder.read_manifest()

    @property
    def manifest(self) -> dict[str, Any]:
        """The parsed ``manifest.json`` (docs/run-format.md), read now."""
        return json.loads(dump(self.read_manifest()))

    @property
    def status(self) -> str:
        return self.read_manifest().status

    @property
    def pinned(self) -> bool:
        return self.read_manifest().pinned

    def steps(self, step: str | None = None, item: str | None = None) -> list[StepRecord]:
        """Step records in workflow order (global steps once, item steps per item), optionally filtered."""
        manifest = self.read_manifest()
        records: list[StepRecord] = []
        for info in manifest.steps:
            if step is not None and info.name != step:
                continue
            ids = [i.id for i in manifest.items if i.items_from == info.per]
            for unit_item in [None] if info.kind in RUN_LEVEL else ids:
                if item is None or unit_item == item:
                    records.append(self._record(manifest, info, unit_item))
        return records

    def _record(self, manifest: Manifest, info: StepInfo, item: str | None) -> StepRecord:
        meta = self.folder.read_meta(info.name, item) or StepMetadata(
            run_id=manifest.run_id,
            step=info.name,
            item=item,
            kind=info.kind,
            status=manifest.state.get(state_key(info.name, item), "pending"),
            version=info.version,
            source_hash=info.source_hash,
            attempt=0,
        )
        data = json.loads(dump(meta))
        return StepRecord(
            **{k: data[k] for k in StepRecord.__dataclass_fields__ if k != "location"},
            location=f"{self.folder.storage.url.rstrip('/')}/{self.folder.step_key(info.name, item)}/",
        )

    def output(
        self,
        step: str,
        item: str | None = None,
        *,
        name: str | None = None,
        local_dir: str | Path | None = None,
    ) -> Any:
        """The current output of a step: the value (a Pydantic model, JSON data, ...) or a local
        ``fk.File`` / ``fk.Dir`` (on remote storage downloaded into ``local_dir`` or a temporary folder).
        """
        meta = self.folder.read_meta(step, item)
        if meta is None or not meta.outputs:
            status = meta.status if meta else "not run"
            raise OutputNotFound(
                f"step {step!r} has no current output for item {item!r} in run {self.run_id} ({status})"
            )
        if name is None and len(meta.outputs) > 1:
            raise HoneFlowError(f"step {step!r} has outputs {sorted(meta.outputs)}; pass name=")
        output = meta.outputs[name or next(iter(meta.outputs))]
        prefix = self.folder.step_key(step, item, "output")
        storage = self.folder.storage
        if isinstance(storage, LocalStorage) and output.type in ("file", "dir"):  # no copy needed
            top = next(iter(output.files)).split("/")[0]
            return (File if output.type == "file" else Dir)(storage.path(f"{prefix}/{top}"))
        local = (
            Path(local_dir) if local_dir is not None else Path(tempfile.mkdtemp(prefix="hone-flow-output-"))
        )
        return load_output(storage, prefix, output, local)

    def require_writable(self, operation: str) -> None:
        """Raise unless this run may change: a pinned copy is an immutable archive."""
        if "/pinned_runs/" in f"/{self.folder.prefix}":
            raise HoneFlowError(
                f"run {self.run_id} is a pinned archive; {operation} is not allowed: fork it instead"
            )

    def require_workflow(self, operation: str, *, archive_ok: bool = False) -> Workflow:
        """The workflow of an attached run (and not a pinned archive unless ``archive_ok``)."""
        if not archive_ok:
            self.require_writable(operation)
        if self._wf is None:
            raise HoneFlowError(f"{operation} needs the workflow's code: use wf.open_run({self.run_id!r})")
        return self._wf

    def resume(
        self,
        *,
        until: str | None = None,
        items: Sequence[str] | None = None,
        trace: Mapping[str, str] | None = None,
        on_event: OnEvent | None = None,
    ) -> Run:
        """Continue this run (same run id): run its pending, failed, interrupted, blocked and skipped work.

        ``until`` / ``items`` restrict the call (work outside stays ``skipped``). Raises ``IncompatibleRun``
        (and runs nothing) when the workflow changed in a way that would mix step versions.
        ``on_event`` receives progress events (``hone_flow.progress``).
        """
        wf = self.require_workflow("resume")
        with RunLease(self.folder.storage, self.folder.prefix):
            recover(self.folder, wf.sink)
            manifest = self.read_manifest()
            warnings = check_resumable(wf, manifest)
            wf.check_call(
                [item_from_info(i) for i in manifest.items if i.items_from is None],
                manifest.params,
                until,
                selected=items,
                produced=[i.id for i in manifest.items if i.items_from is not None],
            )
            refuse_not_selected(manifest, items)
            call = open_call(
                wf,
                self.folder,
                kind="resume",
                params=manifest.params,
                until=until,
                items=items,
                trace=trace,
                on_event=on_event,
            )
            call.manifest.warnings += warnings
            call.events += [{"name": "warning", "time": w["at"], "attributes": w} for w in warnings]
            execute(call)
        return self

    @overload
    def fork(
        self,
        refresh: Sequence[str] | str | Mapping[str, Sequence[str] | None] = (),
        *,
        items: Sequence[str | Item] | None = None,
        params: Mapping[str, Any] | None = None,
        dry_run: Literal[False] = False,
        trace: Mapping[str, str] | None = None,
        label: str | None = None,
        description: str | None = None,
        on_event: OnEvent | None = None,
    ) -> Run: ...

    @overload
    def fork(
        self,
        refresh: Sequence[str] | str | Mapping[str, Sequence[str] | None] = (),
        *,
        items: Sequence[str | Item] | None = None,
        params: Mapping[str, Any] | None = None,
        dry_run: Literal[True],
        trace: Mapping[str, str] | None = None,
        label: str | None = None,
        description: str | None = None,
        on_event: OnEvent | None = None,
    ) -> ForkPlan: ...

    def fork(
        self,
        refresh: Sequence[str] | str | Mapping[str, Sequence[str] | None] = (),
        *,
        items: Sequence[str | Item] | None = None,
        params: Mapping[str, Any] | None = None,
        dry_run: bool = False,
        trace: Mapping[str, str] | None = None,
        label: str | None = None,
        description: str | None = None,
        on_event: OnEvent | None = None,
    ) -> Run | ForkPlan:
        """A new run beside this one that copies unchanged results and reruns the rest (design §4.7).

        ``refresh`` names steps to rerun (with everything downstream), or maps step names to the item
        ids to rerun them for (``{"script": ["02"]}``); without it the fork reruns
        exactly what changed since this run (versions, source, params, item inputs) and its downstream.
        ``items``: ids of this run's items and/or ``fk.Item`` objects (new inputs or new items).
        ``params`` are merged over this run's params. ``dry_run=True`` returns the ``ForkPlan``.
        ``label`` / ``description`` name the new run (default: ``"<this label> (fork)"``, this description).
        """
        return fork.fork(
            self,
            refresh,
            items=items,
            params=params,
            dry_run=dry_run,
            trace=trace,
            label=label,
            description=description,
            on_event=on_event,
        )

    def set_label(self, label: str | None, description: str | None = None) -> None:
        """Name this run for people: ``label`` replaces the current one (``None`` removes it);
        ``description=None`` keeps the current description. Takes the run lease (``RunLocked`` while a
        process runs it; inside a step use ``ctx.set_run_label``)."""
        self.require_writable("set_label")
        with RunLease(self.folder.storage, self.folder.prefix) as lease:
            if lease.taken_over:
                recover(self.folder, self.sink)
            manifest = self.read_manifest()
            apply_label(manifest, label, description)
            self.folder.write_manifest(manifest)

    def pin(self) -> None:
        """Copy this finished run to ``pinned_runs/<id>/`` (every file verified); cleanup never deletes it."""
        from hone_flow.history import pin  # noqa: PLC0415 - history.py imports this module

        pin(self)

    def retry_notifications(self) -> list[Delivery]:
        """Send every pending and failed notification delivery of this run once more (no background retry)."""
        self.require_writable("retry_notifications")
        with RunLease(self.folder.storage, self.folder.prefix) as lease:
            if lease.taken_over:
                recover(self.folder, self.sink)
            return notifications.retry(self.folder)

    @property
    def sink(self) -> RecordSink | None:
        """The workflow's extra span sink (attached runs), which also receives review spans."""
        return self._wf.sink if self._wf is not None else None

    def approve(
        self,
        step: str,
        item: str | None = None,
        *,
        note: str = "",
        actor: str | None = None,
        automated: bool = False,
    ) -> None:
        """Accept a gate's output for an item; ``resume()`` then continues downstream.

        ``actor`` defaults to ``$USER``; ``automated=True`` records that a program (a script, a model)
        decided, not a person (``actor_kind``).
        """
        reviews.approve(self, step, item, note=note, actor=actor, automated=automated)

    def edit(
        self,
        step: str,
        item: str | None = None,
        *,
        value: Any,
        actor: str | None = None,
        automated: bool = False,
    ) -> None:
        """Replace a gate's output with ``value``; ``resume()`` continues downstream with it."""
        reviews.edit(self, step, item, value=value, actor=actor, automated=automated)

    def reject(
        self,
        step: str,
        item: str | None = None,
        *,
        note: str,
        actor: str | None = None,
        automated: bool = False,
    ) -> None:
        """Send the gate's producers back with ``note``; ``resume()`` reruns them (``review_note``)."""
        reviews.reject(self, step, item, note=note, actor=actor, automated=automated)

    def lease(self) -> LeaseInfo | None:
        """Who holds this run now (``lease.json``), and whether that holder is ``live``; ``None`` when no
        process holds it. A ``running`` run whose lease is not live was left by a process that died."""
        found = read_lease(self.folder.storage, self.folder.key("lease.json"))
        if found is None:
            return None
        held = found[0]
        return LeaseInfo(
            host=held.host,
            pid=held.pid,
            acquired_at=held.acquired_at,
            heartbeat_at=held.heartbeat_at,
            expires_at=held.expires_at,
            live=is_live(held),
        )

    def spans(self) -> list[dict[str, Any]]:
        """The spans of every call on this run (``spans.jsonl``)."""
        try:
            data = self.folder.storage.read_bytes(self.folder.key("spans.jsonl"))
        except FileNotFoundError:
            return []
        return [json.loads(line) for line in data.decode().splitlines() if line.strip()]
