"""Contract checkers for hone-flow's ports. Providers run them against their adapters."""

from __future__ import annotations

from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from hone_flow.errors import WriteConflict
from hone_flow.ports import GpuLease, RecordSink, RunStorage
from hone_flow.serialize import sha256_bytes


def check_gpu_lease(g: GpuLease) -> None:
    """The lease can be taken, and taken again inside itself (reentrant)."""
    with g.lease("contract-test", 0.1, timeout_s=5), g.lease("contract-test", 0.1, timeout_s=5):
        pass


def example_span() -> dict[str, Any]:
    """A minimal example span in the honeworks span format."""
    return {
        "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
        "span_id": "00f067aa0ba902b7",
        "parent_span_id": None,
        "name": "hone.test.example",
        "kind": "internal",
        "start_time": "2026-09-27T14:03:11.120Z",
        "end_time": "2026-09-27T14:03:11.220Z",
        "status": {"code": "ok", "message": ""},
        "attributes": {"hone.schema_version": "1"},
        "events": [],
        "resource": {},
        "links": [],
    }


def check_record_sink(sink: RecordSink, read_back: Callable[[], Iterable[Mapping[str, Any]]]) -> None:
    """An emitted span can be read back after ``flush()``."""
    span = example_span()
    sink.emit(span)
    sink.flush()
    got = [s for s in read_back() if s["span_id"] == span["span_id"]]
    assert got, "the emitted span must be readable after flush()"
    assert got[0]["trace_id"] == span["trace_id"]


def check_run_storage(storage: RunStorage, tmp_dir: Path) -> None:
    """The ``RunStorage`` semantics hone-flow relies on (design §4.2). ``tmp_dir``: an empty local folder."""
    base = "contract/run"
    with _expect_raises(FileNotFoundError):
        storage.read_bytes(f"{base}/missing.json")
    assert not storage.exists(f"{base}/a.json")
    storage.write_bytes(f"{base}/a.json", b"one", if_absent=True)
    assert storage.read_bytes(f"{base}/a.json") == b"one"
    with _expect_raises(WriteConflict):
        storage.write_bytes(f"{base}/a.json", b"two", if_absent=True)
    with _expect_raises(WriteConflict):
        storage.write_bytes(f"{base}/a.json", b"two", if_match=sha256_bytes(b"not what is there"))
    with _expect_raises(WriteConflict):  # a missing key matches no content, not even empty content
        storage.write_bytes(f"{base}/missing.json", b"x", if_match=sha256_bytes(b""))
    storage.write_bytes(f"{base}/a.json", b"two", if_match=sha256_bytes(b"one"))
    assert storage.read_bytes(f"{base}/a.json") == b"two"
    storage.write_bytes(f"{base}/empty", b"")
    assert storage.read_bytes(f"{base}/empty") == b""
    assert storage.exists(f"{base}/empty")
    storage.delete(f"{base}/empty")
    local = tmp_dir / "in.bin"
    local.write_bytes(b"\x00big file")
    storage.upload(local, f"{base}/out/file.bin")
    storage.copy(f"{base}/out/file.bin", f"{base}/copy/file.bin")
    storage.download(f"{base}/copy/file.bin", tmp_dir / "back" / "file.bin")
    assert (tmp_dir / "back" / "file.bin").read_bytes() == b"\x00big file"
    assert storage.exists(f"{base}/copy/file.bin")
    assert not storage.exists(f"{base}/copy")  # a folder prefix is not a key
    storage.write_bytes(f"{base}/out/file.bin", b"changed")
    assert storage.read_bytes(f"{base}/copy/file.bin") == b"\x00big file"  # copies, not references
    storage.write_bytes(f"{base}2/sibling.json", b"{}")
    for missing in (
        lambda: storage.copy(f"{base}/nope", f"{base}/x"),
        lambda: storage.download(f"{base}/nope", tmp_dir / "nope"),
    ):
        with _expect_raises(FileNotFoundError):
            missing()
    assert storage.list(base) == [f"{base}/a.json", f"{base}/copy/file.bin", f"{base}/out/file.bin"]
    _check_read_methods(storage, base)
    assert storage.list(f"{base}/a.json") == [f"{base}/a.json"]
    assert f"{base}/a.json" in storage.list("")  # the empty prefix lists everything
    assert storage.list("contract/nothing") == []
    storage.delete(f"{base}/out")
    assert storage.list(base) == [f"{base}/a.json", f"{base}/copy/file.bin"]
    storage.delete(f"{base}/a.json")
    storage.delete(base)
    assert storage.list(base) == []
    storage.delete(base)  # deleting what is gone is fine
    assert storage.list(f"{base}2") == [f"{base}2/sibling.json"]
    storage.delete(f"{base}2")


def _check_read_methods(storage: RunStorage, base: str) -> None:
    """The optional browser methods (``list_dir``, ``size``, ``read_range``), when the storage has them;
    the fallbacks in ``hone_flow.storage`` give the same answers either way."""
    from hone_flow.ports import Entry  # noqa: PLC0415 - keep this module's imports small
    from hone_flow.storage import file_size, list_dir, read_range  # noqa: PLC0415

    expected = [Entry("a.json", False, 3), Entry("copy", True), Entry("out", True)]
    assert list_dir(storage, base) == expected, list_dir(storage, base)
    assert list_dir(storage, f"{base}/copy") == [Entry("file.bin", False, 9)]
    assert list_dir(storage, "contract/nothing") == []
    assert [e.name for e in list_dir(storage, "contract")] == ["run", "run2"]
    assert file_size(storage, f"{base}/copy/file.bin") == 9
    assert read_range(storage, f"{base}/copy/file.bin", 1, 3) == b"big"
    assert read_range(storage, f"{base}/copy/file.bin", 5, 100) == b"file"  # shorter at the end
    assert read_range(storage, f"{base}/copy/file.bin", 2, 0) == b""
    for missing in (
        lambda: file_size(storage, f"{base}/nope"),
        lambda: read_range(storage, f"{base}/nope", 0, 1),
    ):
        with _expect_raises(FileNotFoundError):
            missing()


@contextmanager
def _expect_raises(kind: type[BaseException]) -> Generator[None]:
    """Like ``pytest.raises``, without pytest (the checkers run in any test framework)."""
    try:
        yield
    except kind:
        return
    raise AssertionError(f"expected {kind.__name__}")
