"""The run lease: ``<run>/lease.json`` lets one process at a time change a run (design §4.4).

A lease is live when its host is this host and its pid is alive, or its host is another host and
``expires_at`` is in the future. A live lease held by someone else raises ``RunLocked``; a stale one is
taken over (a conditional write on the content that was read). While held, a daemon thread refreshes
``heartbeat_at`` / ``expires_at`` every ``HEARTBEAT_S`` seconds.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from datetime import UTC, datetime, timedelta
from types import TracebackType

from hone_flow._tracing import iso_now, iso_time
from hone_flow.errors import RunLocked, WriteConflict
from hone_flow.ports import RunStorage
from hone_flow.run_format import Lease, dump, parse
from hone_flow.serialize import sha256_bytes

logger = logging.getLogger("hone_flow")

TTL_S = 60.0
HEARTBEAT_S = 10.0


def read_lease(storage: RunStorage, key: str) -> tuple[Lease, bytes] | None:
    try:
        data = storage.read_bytes(key)
    except FileNotFoundError:
        return None
    return parse(Lease, data, key), data


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # alive, owned by another user
        return True
    return True


def is_live(lease: Lease) -> bool:
    if lease.host == socket.gethostname():
        return pid_alive(lease.pid)
    return datetime.fromisoformat(lease.expires_at) > datetime.now(UTC)


class RunLease:
    """Hold a run's lease inside a ``with`` block; ``taken_over`` says whether a stale lease was replaced.

    >>> from hone_flow.testing import MemoryStorage
    >>> with RunLease(MemoryStorage(), "w/runs/r1") as lease:
    ...     lease.taken_over
    False
    """

    def __init__(self, storage: RunStorage, run_prefix: str) -> None:
        self.storage = storage
        self.key = f"{run_prefix}/lease.json"
        self.owner = str(uuid.uuid4())
        self.taken_over = False
        self.acquired_at = ""
        self._sha = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._heartbeat, name="hone-flow-lease", daemon=True)

    def __enter__(self) -> RunLease:
        found = read_lease(self.storage, self.key)
        self.acquired_at = iso_now()
        if found is None:
            self._write(if_absent=True)
        else:
            held, data = found
            if is_live(held):
                raise RunLocked(
                    f"run {self.key.removesuffix('/lease.json')} is held by pid {held.pid} on {held.host} "
                    f"until {held.expires_at}; wait for it to finish (or stop that process)"
                )
            self._write(if_match=sha256_bytes(data))
            self.taken_over = True
        self._thread.start()
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._stop.set()
        self._thread.join()
        found = read_lease(self.storage, self.key)
        if found is not None and found[0].owner == self.owner:
            self.storage.delete(self.key)

    def _write(self, *, if_match: str | None = None, if_absent: bool = False) -> None:
        now = datetime.now(UTC)
        lease = Lease(
            owner=self.owner,
            host=socket.gethostname(),
            pid=os.getpid(),
            acquired_at=self.acquired_at,
            heartbeat_at=iso_time(now),
            expires_at=iso_time(now + timedelta(seconds=TTL_S)),
        )
        data = dump(lease)
        try:
            self.storage.write_bytes(self.key, data, if_match=if_match, if_absent=if_absent)
        except WriteConflict:
            raise RunLocked(f"another process took the lease of {self.key} first; try again") from None
        self._sha = sha256_bytes(data)

    def _heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self._write(if_match=self._sha)
            except RunLocked:
                logger.error("lost the run lease %s to another process", self.key)
                return
            except Exception:  # a failed refresh must not stop the run; the next one retries
                logger.warning("could not refresh the run lease %s", self.key, exc_info=True)
