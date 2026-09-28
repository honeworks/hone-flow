"""Run storage on the local filesystem (``LocalStorage``), choosing a storage from a setting, and helpers.

Keys are ``/``-separated paths relative to the storage root, e.g.
``song_video/runs/20260927T140311Z-3f9a1c/manifest.json``. ``list`` and ``delete`` take a key or a folder
prefix (``song_video/runs/<id>``): the key itself and every key below ``<prefix>/``.
"""

from __future__ import annotations

import errno
import fcntl
import os
import secrets
import shutil
import tempfile
from collections.abc import Callable, Generator
from contextlib import contextmanager, nullcontext
from pathlib import Path, PurePosixPath

from hone_flow.errors import HoneFlowError, WriteConflict
from hone_flow.ports import Entry, RunStorage
from hone_flow.serialize import sha256_file

READ_ONLY = 0o444
TMP_MARK = ".tmp-"  # temporary files of atomic writes; never listed


class LocalStorage:
    """Run folders under a local folder. Writes are atomic (temp file, fsync, rename).

    Conditional writes are checked under an ``fcntl`` lock on the key's folder. ``copy`` and
    ``download`` make hardlinks when they can; every stored file is read-only (``0444``), so a step
    writing to an input cannot change another run's file through a hardlink.

    >>> import tempfile
    >>> storage = LocalStorage(tempfile.mkdtemp())
    >>> storage.write_bytes("demo/a.txt", b"hi")
    >>> storage.list("demo")
    ['demo/a.txt']
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).absolute()
        self.url = str(self.root)

    def path(self, key: str) -> Path:
        """The local path of ``key`` (keys never leave the root)."""
        parts = PurePosixPath(key).parts
        if ".." in parts or (parts and parts[0] == "/"):
            raise HoneFlowError(f"invalid storage key {key!r}")
        return self.root.joinpath(*parts)

    def read_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def write_bytes(
        self, key: str, data: bytes, *, if_match: str | None = None, if_absent: bool = False
    ) -> None:
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        conditional = if_match is not None or if_absent
        with _folder_lock(path.parent) if conditional else nullcontext():
            if if_absent and path.exists():
                raise WriteConflict(f"{key} already exists")
            if if_match is not None and (not path.exists() or sha256_file(path) != if_match):
                raise WriteConflict(f"{key} changed since it was read (another writer?)")
            _replace(path, lambda tmp: tmp.write_bytes(data))

    def upload(self, local_path: Path, key: str) -> None:
        """Copy a local file in (never a hardlink: the stored copy is made read-only)."""
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        _replace(path, lambda tmp: shutil.copyfile(local_path, tmp))

    def download(self, key: str, local_path: Path) -> None:
        """A copy, not a link: a step that changes its input must not change the stored file."""
        local_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path(key), local_path)

    def copy(self, src_key: str, dst_key: str) -> None:
        _link_or_copy(self.path(src_key), self.path(dst_key))

    def list(self, prefix: str) -> list[str]:
        base = self.path(prefix)
        if base.is_file():
            return [prefix]
        files = [p for p in base.rglob("*") if p.is_file() and TMP_MARK not in p.name]
        return sorted(p.relative_to(self.root).as_posix() for p in files)

    def delete(self, prefix: str) -> None:
        path = self.path(prefix)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.path(key).is_file()

    def list_dir(self, prefix: str) -> list[Entry]:
        """The children of a folder (files with sizes, then folders), sorted by name; ``[]`` if missing."""
        folder = self.path(prefix)
        if not folder.is_dir():
            return []
        found: list[Entry] = []
        with os.scandir(folder) as children:
            for child in children:
                if TMP_MARK in child.name:
                    continue
                is_dir = child.is_dir()
                found.append(Entry(child.name, is_dir, None if is_dir else child.stat().st_size))
        return sorted(found, key=lambda e: e.name)

    def size(self, key: str) -> int:
        path = self.path(key)
        if not path.is_file():
            raise FileNotFoundError(key)
        return path.stat().st_size

    def read_range(self, key: str, start: int, length: int) -> bytes:
        """``length`` bytes from ``start`` (fewer at the end of the file), without reading the rest."""
        with self.path(key).open("rb") as fh:
            fh.seek(start)
            return fh.read(max(length, 0))

    def append(self, key: str, data: bytes) -> None:
        """Append to a file (``spans.jsonl``). A file shared through a hardlink is copied first."""
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_nlink > 1:
            _replace(path, lambda tmp: shutil.copyfile(path, tmp))
        if path.exists():
            path.chmod(0o644)
        with path.open("ab") as fh:
            fh.write(data)
            os.fsync(fh.fileno())
        path.chmod(READ_ONLY)


def _replace(path: Path, write: Callable[[Path], object]) -> None:
    """Write through a temporary sibling file, then atomically rename it over ``path``."""
    tmp = path.with_name(f"{path.name}{TMP_MARK}{secrets.token_hex(4)}")
    try:
        write(tmp)
        with tmp.open("rb") as fh:
            os.fsync(fh.fileno())
        tmp.chmod(READ_ONLY)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink ``src`` to ``dst`` (atomically replacing it); copy across filesystems."""
    if not src.is_file():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}{TMP_MARK}{secrets.token_hex(4)}")
    try:
        os.link(src, tmp)
        os.replace(tmp, dst)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EMLINK):
            raise
        _replace(dst, lambda t: shutil.copyfile(src, t))


@contextmanager
def _folder_lock(folder: Path) -> Generator[None]:
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def open_storage(storage: str | os.PathLike[str] | RunStorage) -> RunStorage:
    """The ``RunStorage`` for a ``storage=`` setting.

    A path or ``file://`` URL gives ``LocalStorage``; any other URL (``s3://``, ``memory://``, ...)
    gives ``FsspecStorage`` (extra ``s3``); a ``RunStorage`` object is used as it is.
    """
    if not isinstance(storage, str | os.PathLike):
        return storage
    text = os.fspath(storage)
    if text.startswith("file://"):
        return LocalStorage(text.removeprefix("file://"))
    if "://" in text:
        from hone_flow.fsspec_storage import FsspecStorage  # noqa: PLC0415 - optional extra, loaded lazily

        return FsspecStorage(text)
    return LocalStorage(text)


def list_dir(storage: RunStorage, prefix: str) -> list[Entry]:
    """``storage.list_dir(prefix)``, or the same derived from the recursive ``list`` (slower)."""
    native = getattr(storage, "list_dir", None)
    if native is not None:
        return list(native(prefix))
    base = prefix.strip("/")
    children: dict[str, Entry] = {}
    for key in storage.list(base):
        rest = key.removeprefix(f"{base}/") if base else key
        if rest == key and base:
            continue  # the prefix is a file, not a folder
        name, _, below = rest.partition("/")
        children[name] = Entry(name, True) if below else Entry(name, False, file_size(storage, key))
    return sorted(children.values(), key=lambda e: e.name)


def file_size(storage: RunStorage, key: str) -> int:
    """``storage.size(key)``, or the length of ``read_bytes`` (slower)."""
    native = getattr(storage, "size", None)
    return int(native(key)) if native is not None else len(storage.read_bytes(key))


def read_range(storage: RunStorage, key: str, start: int, length: int) -> bytes:
    """``storage.read_range(...)``, or a slice of ``read_bytes`` (reads the whole file)."""
    native = getattr(storage, "read_range", None)
    if native is not None:
        return bytes(native(key, start, length))
    return storage.read_bytes(key)[start : start + max(length, 0)]


def upload_tree(storage: RunStorage, folder: Path, prefix: str) -> None:
    """Store every file below ``folder`` under ``prefix`` (same relative paths)."""
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            storage.upload(path, f"{prefix}/{path.relative_to(folder).as_posix()}")


def download_tree(storage: RunStorage, prefix: str, folder: Path) -> None:
    """Fetch every key below ``prefix`` into ``folder`` (same relative paths)."""
    folder.mkdir(parents=True, exist_ok=True)
    for key in storage.list(prefix):
        storage.download(key, folder / key.removeprefix(prefix).lstrip("/"))


def key_sha256(storage: RunStorage, key: str) -> str:
    """sha256 of a stored file, streamed through a temporary local copy (files can be large)."""
    if isinstance(storage, LocalStorage):
        return sha256_file(storage.path(key))
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "file"
        storage.download(key, local)
        return sha256_file(local)
