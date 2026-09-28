"""Fakes for every port hone-flow owns. Deterministic, in memory, and they record their calls."""

from __future__ import annotations

import threading
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any


class FakeGpuLease:
    """``GpuLease`` that grants immediately and records ``("enter", name, vram_gb)`` / ``("exit", name)``.

    Reentrant: nested leases of the same name in the same thread are recorded once.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.traces: list[Mapping[str, str] | None] = []
        self._depth: dict[tuple[str, int], int] = {}

    @contextmanager
    def lease(
        self,
        name: str,
        vram_gb: float,
        *,
        timeout_s: float | None = None,
        trace: Mapping[str, str] | None = None,
    ) -> Generator[None]:
        key = (name, threading.get_ident())
        outer = self._depth.get(key, 0) == 0
        if outer:
            self.calls.append(("enter", name, vram_gb))
            self.traces.append(trace)
        self._depth[key] = self._depth.get(key, 0) + 1
        try:
            yield
        finally:
            self._depth[key] -= 1
            if outer:
                self.calls.append(("exit", name))
