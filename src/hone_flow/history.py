"""The runs of one workflow in one storage: listings, opening runs, pinning and cleanup (design §4.11,
§4.14). Everything here works from the run folders alone, without the workflow's code.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from hone_flow._tracing import iso_now
from hone_flow.commit import recover
from hone_flow.errors import HoneFlowError, RunLocked, RunNotFound
from hone_flow.ports import RunStorage
from hone_flow.run import Run
from hone_flow.run_format import Manifest, RunFolder
from hone_flow.run_lease import RunLease, is_live, read_lease
from hone_flow.storage import LocalStorage, key_sha256, list_dir, open_storage
from hone_flow.types import CleanupReport, RunSummary

if TYPE_CHECKING:
    from hone_flow.workflow import Workflow


READ_WORKERS = 16  # manifests read at once on remote storage


class RunHistory:
    """The runs of workflow ``name`` under ``storage``, readable without the workflow's code.

    >>> from hone_flow.testing import MemoryStorage
    >>> open_runs(MemoryStorage(), "song_video").runs()
    []
    """

    def __init__(self, storage: RunStorage, name: str, workflow: Workflow | None = None) -> None:
        self.storage = storage
        self.name = name
        self._wf = workflow

    def _ids(self, folder: str) -> list[str]:
        """The run folders under ``<name>/<folder>/``, from a one-level listing."""
        return [e.name for e in list_dir(self.storage, f"{self.name}/{folder}") if e.is_dir]

    def run_ids(self) -> list[str]:
        """Every run id, newest first, from one-level listings of ``runs/`` and ``pinned_runs/``.

        Reads no manifest, so it is cheap on S3 however many files the runs hold; a page of a run table
        then reads only its own runs with ``summaries``. Ids start with their creation time (to the
        second). A run being created may be listed before its manifest exists (``summaries`` skips it).
        """
        return sorted(set(self._ids("runs")) | set(self._ids("pinned_runs")), reverse=True)

    def summaries(self, run_ids: Iterable[str]) -> list[RunSummary]:
        """The ``RunSummary`` of each given run, in the given order, reading only those manifests (in
        parallel on remote storage). Ids without a manifest (being created, or deleted) are left out."""
        ids = list(run_ids)
        if isinstance(self.storage, LocalStorage) or len(ids) < 2:
            found = [self._summary(run_id) for run_id in ids]
        else:
            with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
                found = list(pool.map(self._summary, ids))
        return [summary for summary in found if summary is not None]

    def _summary(self, run_id: str) -> RunSummary | None:
        pinned = self.storage.exists(f"{self.name}/pinned_runs/{run_id}/manifest.json")
        for folder in ("runs", "pinned_runs"):  # the runs/ copy wins while it exists
            run_folder = RunFolder(self.storage, f"{self.name}/{folder}/{run_id}")
            try:
                manifest = run_folder.read_manifest()
            except HoneFlowError:
                if self.storage.exists(run_folder.key("manifest.json")):
                    raise  # there, but unreadable (e.g. a newer format_version): say so
                continue
            return _summary(manifest, run_folder.location, pinned)
        return None

    def runs(self, *, updated_since: str | datetime | None = None) -> list[RunSummary]:
        """One summary per logical run (a pinned copy and its original are one run), newest first.

        ``updated_since`` (ISO-8601 or a datetime) keeps runs whose manifest changed at or after it.
        This reads every manifest; a table of many runs uses ``run_ids`` and ``summaries`` per page.
        """
        since = _as_time(updated_since) if updated_since is not None else None
        found = [
            s for s in self.summaries(self.run_ids()) if since is None or _as_time(s.updated_at) >= since
        ]
        return sorted(found, key=lambda s: (s.created_at, s.run_id), reverse=True)

    def _newest_first(self, run_ids: Iterable[str]) -> list[tuple[Manifest, str]]:
        """(manifest, prefix) of each run under ``runs/`` that has a manifest, newest first."""
        found: list[tuple[Manifest, str]] = []
        for run_id in run_ids:
            prefix = f"{self.name}/runs/{run_id}"
            if self.storage.exists(f"{prefix}/manifest.json"):
                found.append((RunFolder(self.storage, prefix).read_manifest(), prefix))
        return sorted(found, key=lambda pair: (pair[0].created_at, pair[0].run_id), reverse=True)

    def open_run(self, run_id: str) -> Run:
        """``runs/<id>`` if it exists, else its pinned copy; ``RunNotFound`` if neither exists."""
        for folder in ("runs", "pinned_runs"):
            prefix = f"{self.name}/{folder}/{run_id}"
            if self.storage.exists(f"{prefix}/manifest.json"):
                return Run(self.storage, prefix, self._wf)
        raise RunNotFound(f"no run {run_id!r} of workflow {self.name!r} in {self.storage.url}")

    def cleanup(
        self, *, keep_last: int | None = None, older_than: timedelta | None = None, dry_run: bool = False
    ) -> CleanupReport:
        """Delete run folders under ``runs/`` outside the newest ``keep_last`` and older than ``older_than``
        (both must hold when both are given). Never ``pinned_runs/``, never a run with a live lease."""
        if keep_last is None and older_than is None:
            raise HoneFlowError("cleanup needs keep_last=, older_than= or both")
        if keep_last is not None and keep_last < 0:
            raise HoneFlowError(f"keep_last must be 0 or more, not {keep_last}")
        runs = self._newest_first(self._ids("runs"))
        cutoff = datetime.now(UTC) - older_than if older_than is not None else None
        deleted: list[str] = []
        kept: list[str] = []
        locked: list[str] = []
        for index, (manifest, prefix) in enumerate(runs):
            old_enough = cutoff is None or _as_time(manifest.created_at) < cutoff
            if (keep_last is not None and index < keep_last) or not old_enough:
                kept.append(manifest.run_id)
            elif _live_lease(self.storage, prefix):
                locked.append(manifest.run_id)
            elif dry_run or self._delete(prefix):
                deleted.append(manifest.run_id)
            else:  # a process took the run between the check and the delete
                locked.append(manifest.run_id)
        return CleanupReport(tuple(deleted), tuple(kept), tuple(locked))

    def _delete(self, prefix: str) -> bool:
        """Delete a run folder while holding its lease; ``False`` when another process holds it."""
        try:
            with RunLease(self.storage, prefix):
                self.storage.delete(prefix)
        except RunLocked:
            return False
        return True


def open_runs(storage: str | os.PathLike[str] | RunStorage, name: str) -> RunHistory:
    """Read the runs of workflow ``name`` under ``storage`` without importing the workflow.

    Opened runs are *detached*: they can be read, reviewed, pinned and retry notifications, but
    ``resume()`` / ``fork()`` need the workflow (``wf.open_run``).
    """
    return RunHistory(open_storage(storage), name)


def pin(run: Run) -> None:
    """Copy a finished run to ``pinned_runs/<id>/``, verify every file's sha256, mark both copies pinned.

    The pinned ``manifest.json`` is written last: until then the copy is not listed or opened, so a crash
    part way never leaves a half archive that looks complete.
    """
    storage, source = run.folder.storage, run.folder.prefix
    target = RunFolder(storage, source.replace("/runs/", "/pinned_runs/", 1))
    if "/pinned_runs/" in f"/{source}" or storage.exists(target.key("manifest.json")):
        raise HoneFlowError(f"run {run.run_id} is already pinned (pinned copies never change)")
    with RunLease(storage, source) as lease:
        if lease.taken_over:
            recover(run.folder, run.sink)
        manifest = run.folder.read_manifest()
        if manifest.status not in ("completed", "failed"):
            raise HoneFlowError(
                f"run {run.run_id} is {manifest.status}; only completed or failed runs can be pinned"
            )
        storage.delete(target.prefix)  # debris of a pin that failed part way
        try:
            _copy_verified(storage, source, target.prefix)
        except Exception:
            storage.delete(target.prefix)
            raise
        manifest.pinned, manifest.pinned_at = True, iso_now()
        run.folder.write_manifest(manifest)
        target.write_manifest(manifest.model_copy(update={"location": target.location}), create=True)


def _copy_verified(storage: RunStorage, source: str, target: str) -> None:
    """Copy every file of a run folder except its lease and manifest, checking each copy's sha256."""
    for key in storage.list(source):
        if key not in (f"{source}/lease.json", f"{source}/manifest.json"):
            copy = target + key.removeprefix(source)
            storage.copy(key, copy)
            if key_sha256(storage, copy) != key_sha256(storage, key):
                raise HoneFlowError(
                    f"the pinned copy of {key} does not match the original; nothing was pinned"
                )


def _summary(manifest: Manifest, location: str, pinned: bool) -> RunSummary:
    return RunSummary(
        run_id=manifest.run_id,
        workflow=manifest.workflow,
        workflow_version=manifest.workflow_version,
        status=manifest.status,
        created_at=manifest.created_at,
        updated_at=manifest.updated_at,
        fork_of=manifest.fork_of.run_id if manifest.fork_of else None,
        pinned=pinned,
        items=tuple(i.id for i in manifest.items),
        location=location,
        label=manifest.label,
        description=manifest.description,
        attempts=dict(manifest.attempts),
    )


def _as_time(value: str | datetime) -> datetime:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError:
        raise HoneFlowError(f"{value!r} is not an ISO-8601 time such as 2026-09-01T00:00:00Z") from None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _live_lease(storage: RunStorage, prefix: str) -> bool:
    found = read_lease(storage, f"{prefix}/lease.json")
    return found is not None and is_live(found[0])
