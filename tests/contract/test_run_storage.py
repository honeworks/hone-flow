import os
import stat
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.testing import MemoryStorage
from hone_flow.testing.contracts import check_run_storage


def test_memory_storage_contract(tmp_path: Path) -> None:
    check_run_storage(MemoryStorage(), tmp_path)


def test_local_storage_contract(tmp_path: Path) -> None:
    check_run_storage(fk.LocalStorage(tmp_path / "root"), tmp_path)


def test_fsspec_memory_storage_contract(tmp_path: Path) -> None:
    check_run_storage(fk.FsspecStorage(f"memory://contract-{os.getpid()}/proj"), tmp_path)


def test_local_storage_files_are_read_only_hardlinks(tmp_path: Path) -> None:
    storage = fk.LocalStorage(tmp_path / "root")
    source = tmp_path / "user.txt"
    source.write_text("mine")
    storage.upload(source, "w/a/in.txt")
    assert os.stat(source).st_mode & stat.S_IWUSR  # the user's file keeps its permissions (a copy)
    assert os.stat(source).st_ino != os.stat(storage.path("w/a/in.txt")).st_ino
    storage.copy("w/a/in.txt", "w/b/in.txt")
    stored = storage.path("w/b/in.txt")
    assert os.stat(stored).st_ino == os.stat(storage.path("w/a/in.txt")).st_ino  # hardlink
    assert stat.S_IMODE(os.stat(stored).st_mode) == 0o444
    with pytest.raises(PermissionError):
        stored.write_text("changed")


def test_local_storage_rejects_keys_leaving_the_root(tmp_path: Path) -> None:
    storage = fk.LocalStorage(tmp_path)
    with pytest.raises(fk.HoneFlowError, match="invalid storage key"):
        storage.read_bytes("../etc/passwd")


def test_local_storage_never_lists_temporary_files(tmp_path: Path) -> None:
    storage = fk.LocalStorage(tmp_path)
    storage.write_bytes("w/m.json", b"{}")
    (tmp_path / "w" / "m.json.tmp-dead").write_text("half written")  # a crash mid-write
    assert storage.list("w") == ["w/m.json"]


def test_open_storage_picks_the_implementation(tmp_path: Path) -> None:
    from hone_flow.storage import open_storage

    assert isinstance(open_storage(tmp_path), fk.LocalStorage)
    assert isinstance(open_storage(str(tmp_path)), fk.LocalStorage)
    local = open_storage(f"file://{tmp_path}")
    assert isinstance(local, fk.LocalStorage)
    assert local.root == tmp_path
    assert isinstance(open_storage("memory://bucket/x"), fk.FsspecStorage)
    memory = MemoryStorage()
    assert open_storage(memory) is memory


def test_fsspec_storage_without_the_extra_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "fsspec", None)
    with pytest.raises(fk.HoneFlowError, match=r"pip install 'hone-flow\[s3\]'"):
        fk.FsspecStorage("s3://bucket/x")


def test_local_write_that_fails_midway_keeps_the_old_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = fk.LocalStorage(tmp_path)
    storage.write_bytes("w/m.json", b"old")

    def crash(fd: int) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(os, "fsync", crash)
    with pytest.raises(OSError, match="disk gone"):
        storage.write_bytes("w/m.json", b"new")
    assert storage.read_bytes("w/m.json") == b"old"
    assert sorted(p.name for p in (tmp_path / "w").iterdir()) == ["m.json"]


def test_only_one_of_many_racing_if_absent_writers_wins(tmp_path: Path) -> None:
    import threading

    storage = fk.LocalStorage(tmp_path)
    wins: list[bytes] = []
    start = threading.Barrier(8)

    def writer(n: int) -> None:
        start.wait()
        try:
            storage.write_bytes("w/lease.json", str(n).encode(), if_absent=True)
            wins.append(str(n).encode())
        except fk.errors.WriteConflict:
            pass

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1
    assert storage.read_bytes("w/lease.json") == wins[0]


class BareStorage:
    """A storage with only the required ``RunStorage`` methods (no list_dir / size / read_range)."""

    def __init__(self) -> None:
        self._inner = MemoryStorage()
        self.url = "bare"
        for name in ("read_bytes", "write_bytes", "upload", "download", "copy", "list", "delete", "exists"):
            setattr(self, name, getattr(self._inner, name))


def test_storage_without_the_read_methods_uses_the_fallbacks(tmp_path: Path) -> None:
    storage = BareStorage()
    assert not hasattr(storage, "list_dir")
    check_run_storage(storage, tmp_path)  # type: ignore[arg-type]


def test_run_ids_on_a_storage_without_list_dir(tmp_path: Path) -> None:
    storage = BareStorage()
    wf = fk.Workflow("bare", storage=storage)  # type: ignore[arg-type]

    @wf.step()
    def double(x: int) -> int:
        return 2 * x

    first = wf.run([fk.Item("a", {"x": 1})])
    history = fk.open_runs(storage, "bare")  # type: ignore[arg-type]
    assert history.run_ids() == [first.run_id]
    assert [s.run_id for s in history.runs()] == [first.run_id]


def test_local_list_dir_hides_temporary_files(tmp_path: Path) -> None:
    storage = fk.LocalStorage(tmp_path)
    storage.write_bytes("w/m.json", b"{}")
    (tmp_path / "w" / "m.json.tmp-dead").write_text("half written")
    assert storage.list_dir("w") == [fk.Entry("m.json", False, 2)]
    assert storage.list_dir("w/m.json") == []  # a file is not a folder
    with pytest.raises(FileNotFoundError):
        storage.size("w")
