"""``GpuLease`` implementations shipped in the core: a no-op default and a machine-wide file lock."""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from hone_flow.ports import TraceContext


class NullGpuLease:
    """Grants every lease immediately (the default when no ``gpu=`` is given)."""

    @contextmanager
    def lease(
        self, name: str, vram_gb: float, *, timeout_s: float | None = None, trace: TraceContext | None = None
    ) -> Generator[None]:
        yield


class FileLockGpuLease:
    """One exclusive lock file for the whole machine: one lease at a time, whatever ``vram_gb`` says.

    Reentrant within a thread. POSIX only (``fcntl.flock``). The default path is
    ``$HONE_GPU_LEASE_LOCK`` or ``<tmp>/hone-gpu-lease.lock``.

    >>> with FileLockGpuLease().lease("demo", 2.0):
    ...     pass
    """

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        default = Path(tempfile.gettempdir()) / "hone-gpu-lease.lock"
        self.path = Path(path or os.environ.get("HONE_GPU_LEASE_LOCK", default))
        self._held = threading.local()  # per thread: how deep inside a lease we are

    @contextmanager
    def lease(
        self, name: str, vram_gb: float, *, timeout_s: float | None = None, trace: TraceContext | None = None
    ) -> Generator[None]:
        import fcntl  # noqa: PLC0415 - POSIX only; imported when a lease is taken

        depth: int = getattr(self._held, "depth", 0)
        if depth:
            self._held.depth = depth + 1
            try:
                yield
            finally:
                self._held.depth = depth
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as fh:
            deadline = None if timeout_s is None else time.monotonic() + timeout_s
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError(
                            f"GPU lease {name!r}: {self.path} still locked after {timeout_s} s"
                        ) from None
                    time.sleep(0.05)
            self._held.depth = 1
            try:
                yield
            finally:
                self._held.depth = 0
                fcntl.flock(fh, fcntl.LOCK_UN)
