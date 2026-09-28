"""The run folder format (``format_version: "1"``): models of ``manifest.json``, ``metadata.json`` and
``lease.json``, and ``RunFolder``, which reads and writes them for one run.

The format is public and documented in ``docs/run-format.md`` (a test keeps the two in step). Readers
ignore unknown keys; adding optional keys keeps version ``"1"``.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from hone_flow._records import strip_secrets
from hone_flow._tracing import iso_now
from hone_flow.errors import HoneFlowError
from hone_flow.ports import RunStorage
from hone_flow.serialize import canonical_json, load_value, sha256_bytes, sha256_file
from hone_flow.storage import download_tree
from hone_flow.types import Dir, File, Item

FORMAT_VERSION = "1"


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class FileInfo(Model):
    sha256: str
    size: int


class OutputInfo(Model):
    type: str  # json, pydantic:<module>:<qualname>, file, dir, custom:<serializer name>
    files: dict[str, FileInfo] = {}  # paths relative to output/


class InputInfo(Model):
    # step:<name>, global:<name>, item_input, item_value; written as "from" (a Python keyword)
    source: str = Field(validation_alias=AliasChoices("from", "source"), serialization_alias="from")
    files: dict[str, FileInfo] = {}  # paths relative to inputs/


class ErrorInfo(Model):
    message: str
    traceback: str


class Review(Model):
    decision: str  # approved, edited, rejected
    actor: str
    actor_kind: str = "person"  # person or automated (a script or a model decided; change 0010)
    note: str = ""
    at: str
    attempt: int


class Attempt(Model):
    attempt: int
    status: str  # failed, rejected, replaced
    started_at: str | None = None
    ended_at: str | None = None
    note: str | None = None
    error: ErrorInfo | None = None
    outputs: dict[str, OutputInfo] = {}


class StepMetadata(Model):
    """``<step>[/item_<id>]/metadata.json``: the latest attempt at the top, earlier ones in ``attempts``."""

    format_version: str = FORMAT_VERSION
    run_id: str
    step: str
    item: str | None
    kind: str  # step, gate, global
    status: str
    version: str
    source_hash: str
    deterministic: bool = True
    resources: str = "cpu"
    params: dict[str, Any] = {}
    seed: int = 0
    attempt: int = 1
    inputs: dict[str, InputInfo] = {}
    outputs: dict[str, OutputInfo] = {}
    labels: list[str] = []
    reused_from: str | None = None
    source_attempt: int | None = None
    review_note: str | None = None
    reviews: list[Review] = []
    error: ErrorInfo | None = None
    attempts: list[Attempt] = []
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    measurements: dict[str, Any] = {}
    span_id: str | None = None


class ItemInput(Model):
    kind: str  # json, file, dir
    value: Any = None  # json
    path: str | None = None  # file / dir: the absolute original path
    sha256: str | None = None
    size: int | None = None
    snapshot: str | None = None  # a fork whose original is gone: the source run's copy (a storage key)


class ItemInfo(Model):
    id: str
    inputs: dict[str, ItemInput] = {}
    items_from: str | None = None  # the global step that produced this item (change 0005)


class StepInfo(Model):
    name: str
    kind: str
    version: str
    source_hash: str
    resources: str = "cpu"
    deterministic: bool = True
    inputs: list[str] = []  # parameters fed by other steps or item inputs
    outputs: list[str] = []
    per: str | None = None  # an item step over the items this global step produced (change 0005)


class DeliveryInfo(Model):
    state: str  # pending, delivered, failed
    attempts: int = 0
    error: str | None = None
    at: str | None = None
    kind: str = "http"  # slack, mattermost, discord, http
    url_env: str = ""  # the environment variable holding the URL (never the URL)
    browse_url: str | None = None


class NotificationInfo(Model):
    id: str
    event: str
    created_at: str
    summary: str
    deliveries: dict[str, DeliveryInfo] = {}


class CallInfo(Model):
    kind: str  # run, resume, fork
    started_at: str
    ended_at: str | None = None
    host: str
    pid: int
    until: str | None = None
    items: list[str] | None = None
    span_id: str | None = None


class PlanRowInfo(Model):
    item: str | None
    step: str
    action: str  # reuse, run
    reason: str


class ForkInfo(Model):
    run_id: str
    plan: list[PlanRowInfo] = []


class Manifest(Model):
    """``manifest.json``: the run's identity, items, steps, state summary, notifications and calls."""

    format_version: str = FORMAT_VERSION
    run_id: str
    workflow: str
    workflow_version: str
    storage: str
    location: str
    hone_flow_version: str
    created_at: str
    updated_at: str
    status: str
    seed: int = 0
    trace_id: str
    params: dict[str, Any] = {}
    items: list[ItemInfo] = []
    steps: list[StepInfo] = []
    state: dict[str, str] = {}  # "<step>" or "<step>/<item>" -> step state
    measure: list[str] = []
    warnings: list[dict[str, Any]] = []
    fork_of: ForkInfo | None = None
    pinned: bool = False
    pinned_at: str | None = None
    notifications: list[NotificationInfo] = []
    calls: list[CallInfo] = []
    label: str | None = None  # a human name for the run (change 0009)
    description: str | None = None
    attempts: dict[str, int] = {}  # "<step>[/<item>]" -> latest attempt, for units tried more than once


class Lease(Model):
    """``lease.json``: who holds the run and until when."""

    owner: str
    host: str
    pid: int
    acquired_at: str
    heartbeat_at: str
    expires_at: str


M = TypeVar("M", bound=Model)


def parse(model: type[M], data: bytes, key: str) -> M:
    """Load one of the format's JSON files; an unknown ``format_version`` asks to upgrade hone-flow."""
    version = json.loads(data).get("format_version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        raise HoneFlowError(
            f"{key} has format_version {version!r}; this hone-flow reads {FORMAT_VERSION!r}: "
            "upgrade hone-flow"
        )
    return model.model_validate_json(data)


def dump(model: Model) -> bytes:
    return canonical_json(model.model_dump(mode="json", by_alias=True))


def note_attempt(manifest: Manifest, meta: StepMetadata) -> None:
    """Record a unit's attempt count in the manifest (only units tried more than once; change 0008)."""
    if meta.attempt > 1:
        manifest.attempts[state_key(meta.step, meta.item)] = meta.attempt


def apply_label(manifest: Manifest, label: str | None, description: str | None = None) -> None:
    """Name a run (change 0009): ``label`` replaces the current one (blank or ``None`` removes it);
    ``description=None`` keeps the current description. Secret-looking text is stored as ``***``."""
    manifest.label = _text(label)
    if description is not None:
        manifest.description = _text(description)


def _text(value: str | None) -> str | None:
    return str(strip_secrets(value.strip())) if value and value.strip() else None


def new_run_id() -> str:
    """``<UTC time>-<6 random hex>``: unique and sortable by creation time."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3)


def state_key(step: str, item: str | None) -> str:
    return step if item is None else f"{step}/{item}"


def split_state_key(key: str) -> tuple[str, str | None]:
    step, _, item = key.partition("/")
    return step, item or None


class RunFolder:
    """One run folder's files: keys, the manifest (conditional writes) and step metadata."""

    def __init__(self, storage: RunStorage, prefix: str) -> None:
        self.storage = storage
        self.prefix = prefix  # e.g. song_video/runs/<run id>
        self.location = f"{storage.url.rstrip('/')}/{prefix}/"
        self._manifest_sha: str | None = None

    def key(self, *parts: str) -> str:
        return "/".join((self.prefix, *parts))

    def step_key(self, step: str, item: str | None, *parts: str) -> str:
        """Key of a step folder (``<step>`` or ``<step>/item_<id>``) or of a file inside it."""
        folder = step if item is None else f"{step}/item_{item}"
        return self.key(folder, *parts)

    def read_manifest(self) -> Manifest:
        key = self.key("manifest.json")
        try:
            data = self.storage.read_bytes(key)
        except FileNotFoundError:
            raise HoneFlowError(f"no manifest.json at {self.location}") from None
        self._manifest_sha = sha256_bytes(data)
        return parse(Manifest, data, key)

    def write_manifest(self, manifest: Manifest, *, create: bool = False) -> None:
        """Conditional write: ``WriteConflict`` if the manifest changed since this folder last read it."""
        manifest.updated_at = iso_now()
        data = dump(manifest)
        key = self.key("manifest.json")
        if create:
            self.storage.write_bytes(key, data, if_absent=True)
        elif self._manifest_sha is None:
            raise HoneFlowError(f"{key}: read the manifest before writing it (conditional writes)")
        else:
            self.storage.write_bytes(key, data, if_match=self._manifest_sha)
        self._manifest_sha = sha256_bytes(data)

    def read_meta(self, step: str, item: str | None) -> StepMetadata | None:
        key = self.step_key(step, item, "metadata.json")
        try:
            return parse(StepMetadata, self.storage.read_bytes(key), key)
        except FileNotFoundError:
            return None

    def write_meta(self, meta: StepMetadata) -> None:
        self.storage.write_bytes(self.step_key(meta.step, meta.item, "metadata.json"), dump(meta))


def file_info(path: Path) -> FileInfo:
    return FileInfo(sha256=sha256_file(path), size=path.stat().st_size)


def dir_files(path: Path, prefix: str = "") -> dict[str, FileInfo]:
    """Every file below a local folder, by ``<prefix>/<relative path>``."""
    files = sorted(p for p in path.rglob("*") if p.is_file())
    return {"/".join(filter(None, (prefix, p.relative_to(path).as_posix()))): file_info(p) for p in files}


def digest(files: Mapping[str, FileInfo]) -> str:
    """One sha256 for an input or output: its file's hash, or a hash over all its files' hashes."""
    if len(files) == 1:
        return next(iter(files.values())).sha256
    return sha256_bytes(canonical_json({name: f.sha256 for name, f in sorted(files.items())}))


def load_output(storage: RunStorage, prefix: str, output: OutputInfo, local: Path) -> Any:
    """A stored output (its files under ``prefix``) as a step sees it: the loaded value, or a
    ``File`` / ``Dir`` fetched into the local folder ``local``."""
    top = next(iter(output.files)).split("/")[0] if output.files else ""
    if output.type == "file":
        storage.download(f"{prefix}/{top}", local / top)
        return File(local / top)
    if output.type == "dir":
        download_tree(storage, f"{prefix}/{top}", local / top)
        return Dir(local / top)
    return load_value(storage.read_bytes(f"{prefix}/{top}"), output.type)


def item_info(item: Item) -> ItemInfo:
    """An item as the manifest records it: JSON values, and files / folders with their hash and size."""
    inputs: dict[str, ItemInput] = {}
    for name, value in item.inputs.items():
        if isinstance(value, File):
            info = file_info(value.path)
            inputs[name] = ItemInput(
                kind="file", path=str(value.path.absolute()), sha256=info.sha256, size=info.size
            )
        elif isinstance(value, Dir):
            files = dir_files(value.path)
            size = sum(f.size for f in files.values())
            inputs[name] = ItemInput(
                kind="dir", path=str(value.path.absolute()), sha256=digest(files), size=size
            )
        else:
            try:
                canonical_json(value)
            except (TypeError, ValueError):
                raise HoneFlowError(
                    f"input {name!r} of item {item.id!r} is a {type(value).__name__}: item inputs must be "
                    "JSON-compatible values, fk.File or fk.Dir"
                ) from None
            inputs[name] = ItemInput(kind="json", value=value)
    return ItemInfo(id=item.id, inputs=inputs)


def item_from_info(info: ItemInfo) -> Item:
    """The ``fk.Item`` a manifest records (files and folders at their original paths)."""
    inputs: dict[str, Any] = {}
    for name, value in info.inputs.items():
        if value.kind == "json":
            inputs[name] = value.value
        else:
            inputs[name] = (File if value.kind == "file" else Dir)(value.path or "")
    return Item(info.id, inputs)
