"""The Protocols hone-flow owns: GpuLease, RecordSink, RunStorage and TraceContext (docs/adapters.md)."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

TraceContext = Mapping[str, str]

PORTS_VERSION = "1"


class GpuLease(Protocol):
    """Reserve GPU memory around a block of work."""

    def lease(
        self, name: str, vram_gb: float, *, timeout_s: float | None = None, trace: TraceContext | None = None
    ) -> AbstractContextManager[None]: ...


class RecordSink(Protocol):
    """Where spans go. Sinks never raise into the caller."""

    def emit(self, span: Mapping[str, Any]) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Entry:
    """One child of a storage folder (``RunStorage.list_dir``): a file with its ``size``, or a folder."""

    name: str
    is_dir: bool
    size: int | None = None  # bytes, for files


class RunStorage(Protocol):
    """Where run folders live: files under ``/``-separated keys relative to the storage root.

    hone-flow's own port (not a family port). Implementations: ``LocalStorage`` (core),
    ``FsspecStorage`` (extra ``s3``), ``hone_flow.testing.MemoryStorage``. ``if_match`` is the sha256
    hex of the content the caller last read; ``if_absent=True`` fails when the key exists. A failed
    condition raises ``WriteConflict``. ``list`` and ``delete`` take a key or a folder prefix.

    Optional read methods for browsers (the built-in storages have them; ``hone_flow.storage.list_dir``,
    ``file_size`` and ``read_range`` fall back to ``list`` / ``read_bytes`` for storages without them):
    ``list_dir(prefix) -> list[Entry]`` (the children of a folder, sorted by name; ``[]`` when missing),
    ``size(key) -> int`` and ``read_range(key, start, length) -> bytes`` (``FileNotFoundError`` when
    missing; shorter at the end of the file).
    """

    url: str

    def read_bytes(self, key: str) -> bytes: ...

    def write_bytes(
        self, key: str, data: bytes, *, if_match: str | None = None, if_absent: bool = False
    ) -> None: ...

    def upload(self, local_path: Path, key: str) -> None: ...

    def download(self, key: str, local_path: Path) -> None: ...

    def copy(self, src_key: str, dst_key: str) -> None: ...

    def list(self, prefix: str) -> list[str]: ...

    def delete(self, prefix: str) -> None: ...

    def exists(self, key: str) -> bool: ...
