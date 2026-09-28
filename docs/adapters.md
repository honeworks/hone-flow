# Adapters: bring your own GPU lease, span sink or storage

hone-flow talks to the outside world through three small ports (Python `Protocol`s). Anything with the
right methods plugs in; no base class is needed. Each port has a fake in `hone_flow.testing` and a
contract checker that an implementation should pass.

| Port | What it is for | Built in | Fake | Checker |
|---|---|---|---|---|
| `fk.GpuLease` | reserve the GPU around steps tagged `gpu:…` | `fk.NullGpuLease` (default), `fk.FileLockGpuLease` | `FakeGpuLease` | `check_gpu_lease` |
| `fk.RecordSink` | receive spans in addition to `spans.jsonl` | `fk.JsonlSpanSink`, `fk.SqliteSpanSink`, `fk.MemorySink`, `fk.NullSink` | `fk.MemorySink` | `check_record_sink` |
| `fk.RunStorage` | hold run folders | `fk.LocalStorage`, `fk.FsspecStorage` | `MemoryStorage` | `check_run_storage` |

`GpuLease` and `RecordSink` are honeworks family ports (the same shape in every package; `fk.PORTS_VERSION
== "1"`); `RunStorage` is hone-flow's own. `fk.TraceContext` is `Mapping[str, str]` (a W3C `traceparent`
plus `hone.*` keys). The checkers live in `hone_flow.testing.contracts`; they use plain `assert`, so they
run under any test framework.

Examples: [`gpu_batching.py`](../examples/gpu_batching.py),
[`records_and_traces.py`](../examples/records_and_traces.py), [`s3_storage.py`](../examples/s3_storage.py).

## GpuLease

```text
class GpuLease(Protocol):
    def lease(self, name: str, vram_gb: float, *, timeout_s: float | None = None,
              trace: TraceContext | None = None) -> AbstractContextManager[None]: ...
```

`lease(...)` returns a context manager that holds the reservation while the block runs. hone-flow calls it
once per batch of neighbouring steps with the same `gpu:` resources tag: `name` is the tag (for example
`"gpu:ollama"`) and `vram_gb` the largest `vram_gb` of the batch's steps. A step may take it again inside
the batch with `ctx.gpu_lease(...)`, so a lease must be **reentrant** in one thread. If taking the lease
fails, the call stops with `fk.HoneFlowError` ("could not take the GPU lease ...").

Pass one with `fk.Workflow(..., gpu=...)`:

| `gpu=` | Lease |
|---|---|
| `None` (default) | `fk.NullGpuLease()`: grants every lease at once |
| an object | used as it is |
| `"file_lock"` | `fk.FileLockGpuLease()`, from the `hone.gpu_leases` entry points |
| `"hone_models"` | hone-models' GPU scheduler (when hone-models is installed) |
| any other name | the `GpuLease` a package registered under that name in `hone.gpu_leases` |

`fk.FileLockGpuLease(path=None)` is one exclusive lock file for the whole machine (POSIX `flock`): one
lease at a time whatever `vram_gb` says, reentrant within a thread, `TimeoutError` after `timeout_s`. The
lock file is `path`, else `$HONE_GPU_LEASE_LOCK`, else `<tmp>/hone-gpu-lease.lock`. It keeps several
processes (for example several workflows on one workstation) from loading models on the GPU at once.

A lease of your own, checked by the contract and used by a workflow:

```python
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import hone_flow as fk
from hone_flow.testing.contracts import check_gpu_lease


class LoggingLease:
    """Grants every lease and logs it; a real one would wait for free GPU memory."""

    def __init__(self) -> None:
        self.log: list[str] = []

    @contextmanager
    def lease(
        self,
        name: str,
        vram_gb: float,
        *,
        timeout_s: float | None = None,
        trace: Mapping[str, str] | None = None,
    ) -> Iterator[None]:
        self.log.append(f"take {name} {vram_gb} GB")
        try:
            yield
        finally:
            self.log.append(f"give back {name}")


check_gpu_lease(LoggingLease())

gpu = LoggingLease()
wf = fk.Workflow("gpu_demo", storage=tempfile.mkdtemp(), gpu=gpu)


@wf.step(resources="gpu:ollama", vram_gb=6.0)
def shotlist(lyrics: str) -> str:
    return lyrics.upper()


@wf.step(resources="gpu:ollama", vram_gb=4.0)
def prompts(shotlist: str) -> str:
    return shotlist + "!"


wf.run([fk.Item("01", {"lyrics": "rain"}), fk.Item("02", {"lyrics": "sun"})])
assert gpu.log == ["take gpu:ollama 6.0 GB", "give back gpu:ollama"]  # one lease for the whole batch
```

`hone_flow.testing.FakeGpuLease()` records `("enter", name, vram_gb)` and `("exit", name)` in `calls`
(nested leases of the same name once) and the trace contexts in `traces`.

### The `hone.gpu_leases` entry point

A package offers a lease by name in its `pyproject.toml`; a registered class is instantiated without
arguments:

```toml
[project.entry-points."hone.gpu_leases"]
my_gpu = "my_package.gpu:MyGpuLease"
```

Then `fk.Workflow(..., gpu="my_gpu")` uses it. hone-flow registers `file_lock`; hone-models registers
`hone_models`. An unknown name raises `fk.HoneFlowError` listing the names found.

```python
wf_locked = fk.Workflow("locked", storage=tempfile.mkdtemp(), gpu="file_lock")
assert isinstance(wf_locked.gpu, fk.FileLockGpuLease)
```

## RecordSink

```text
class RecordSink(Protocol):
    def emit(self, span: Mapping[str, Any]) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

`fk.Workflow(..., sink=...)` sends every span to the sink as well as to the run's `spans.jsonl` (the span
shape is in [records](records.md)). hone-flow calls `emit` for each span and `flush` after each step
commit; a sink never raises into the run (hone-flow logs its first failure and counts the rest). The
family sinks take `capture_content=` (default: `$HONE_CAPTURE_CONTENT`, on unless `0`) and strip secrets.

`check_record_sink(sink, read_back)` emits an example span, flushes, and checks that `read_back()` returns
it:

```python
from typing import Any

from hone_flow.testing.contracts import check_record_sink


class ListSink:
    """Keeps spans in a list; a real one might post them to a collector."""

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    def emit(self, span: Mapping[str, Any]) -> None:
        self.spans.append(dict(span))

    def flush(self) -> None:
        """Nothing is buffered."""

    def close(self) -> None:
        """Nothing to release."""


sink = ListSink()
check_record_sink(sink, lambda: sink.spans)
memory_sink = fk.MemorySink()
check_record_sink(memory_sink, lambda: memory_sink.spans)
```

## RunStorage

```text
class RunStorage(Protocol):
    url: str                                                    # the root, for manifests and messages
    def read_bytes(self, key: str) -> bytes: ...                # FileNotFoundError when missing
    def write_bytes(self, key: str, data: bytes, *, if_match: str | None = None,
                    if_absent: bool = False) -> None: ...       # WriteConflict when the condition fails
    def upload(self, local_path: Path, key: str) -> None: ...   # a local file in (streamed)
    def download(self, key: str, local_path: Path) -> None: ... # a stored file out (streamed)
    def copy(self, src_key: str, dst_key: str) -> None: ...     # server-side when possible
    def list(self, prefix: str) -> list[str]: ...               # keys (files) under a prefix, sorted
    def delete(self, prefix: str) -> None: ...                  # a key, or everything under a prefix
    def exists(self, key: str) -> bool: ...                     # a file (a folder prefix is not a key)
```

Keys are `/`-separated and relative to the storage root (`<workflow name>/runs/<run_id>/...`, see
[storage](storage.md)). The rules hone-flow relies on:

- `write_bytes` is atomic for readers. `if_match` is the sha256 hex of the content the caller last read
  (a missing key matches nothing); `if_absent=True` fails if the key exists. A failed condition raises
  `fk.WriteConflict`. The manifest and the run lease depend on these.
- `copy` makes an independent copy: changing the source later must not change the copy.
- `list(prefix)` returns the key itself when `prefix` is a key, else every key below `prefix/`; `list("")`
  lists everything; `delete` accepts the same and deleting what is gone is fine.
- `copy` and `download` of a missing key raise `FileNotFoundError`.

Three **optional** read methods serve browsers and dashboards (the built-in storages have them):

```text
    def list_dir(self, prefix: str) -> list[fk.Entry]: ...      # children of a folder, one level, by name
    def size(self, key: str) -> int: ...                        # bytes of a file
    def read_range(self, key: str, start: int, length: int) -> bytes: ...   # a byte range (video seeking)
```

`fk.Entry(name, is_dir, size)` is one child (`size` is `None` for folders); a missing folder lists as
`[]`; `size` and `read_range` of a missing key raise `FileNotFoundError`; `read_range` returns fewer bytes
at the end of a file. Callers use `hone_flow.storage.list_dir(storage, prefix)`, `file_size(storage,
key)` and `read_range(storage, key, start, length)`, which call these methods when a storage has them and
fall back to `list` / `read_bytes` (slower: a recursive listing, a whole-file read) when it does not.

`check_run_storage(storage, tmp_dir)` checks all of this (the read methods, or their fallbacks) (`tmp_dir`: an empty local folder). A small
storage of your own, checked and used by a workflow:

```python
import hashlib
import threading
from pathlib import Path

from hone_flow.testing.contracts import check_run_storage


class DictStorage:
    """Run folders in a dict. hone_flow.testing.MemoryStorage does this already; a real adapter would
    talk to your object store, database or archive format the same way."""

    def __init__(self) -> None:
        self.url = "dict://runs"
        self.data: dict[str, bytes] = {}
        self.lock = threading.Lock()

    def read_bytes(self, key: str) -> bytes:
        if key not in self.data:
            raise FileNotFoundError(key)
        return self.data[key]

    def write_bytes(
        self, key: str, data: bytes, *, if_match: str | None = None, if_absent: bool = False
    ) -> None:
        with self.lock:  # the check and the write are one step
            if if_absent and key in self.data:
                raise fk.WriteConflict(f"{key} already exists")
            if if_match is not None and self._sha256(key) != if_match:
                raise fk.WriteConflict(f"{key} changed since it was read")
            self.data[key] = bytes(data)

    def _sha256(self, key: str) -> str | None:
        return hashlib.sha256(self.data[key]).hexdigest() if key in self.data else None

    def upload(self, local_path: Path, key: str) -> None:
        self.write_bytes(key, local_path.read_bytes())

    def download(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.read_bytes(key))

    def copy(self, src_key: str, dst_key: str) -> None:
        self.write_bytes(dst_key, self.read_bytes(src_key))

    def list(self, prefix: str) -> list[str]:
        below = prefix.rstrip("/") + "/" if prefix else ""
        return sorted(k for k in self.data if k == prefix or k.startswith(below))

    def delete(self, prefix: str) -> None:
        for key in self.list(prefix):
            del self.data[key]

    def exists(self, key: str) -> bool:
        return key in self.data


check_run_storage(DictStorage(), Path(tempfile.mkdtemp()))

storage = DictStorage()
stored = fk.Workflow("dict_demo", storage=storage)


@stored.step()
def double(x: int) -> int:
    return 2 * x


run = stored.run([fk.Item("a", {"x": 21})])
assert run.output("double", "a") == 42
assert run.location == f"dict://runs/dict_demo/runs/{run.run_id}/"
assert fk.open_runs(storage, "dict_demo").runs()[0].run_id == run.run_id
```

A storage shared between processes or machines must make `if_absent` and `if_match` hold across them
(for example with the backend's conditional writes). When the backend cannot, document it: the run lease
still keeps one writer per run, as with `fk.FsspecStorage` on backends without conditional writes.

## Fakes

| `hone_flow.testing` | For |
|---|---|
| `MemoryStorage()` | run folders in a dict (`files`), exact `RunStorage` semantics, one process |
| `FakeGpuLease()` | records leases in `calls` and trace contexts in `traces` |
| `WebhookServer(status=200, on_request=None)` | a local HTTP server that records webhook requests ([notifications](notifications.md#testing-your-setup)) |
| `contracts` | `check_gpu_lease(lease)`, `check_record_sink(sink, read_back)`, `check_run_storage(storage, tmp_dir)` |

`fk.MemorySink()` (in the main package) keeps spans in `spans` and serves as the `RecordSink` fake.
