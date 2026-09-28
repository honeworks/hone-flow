"""``FsspecStorage``: run folders on S3 or any fsspec filesystem (extra ``s3``; ``memory://`` in tests).

``if_absent`` uses the backend's exclusive create where it has one (S3 conditional writes, memory://),
else checks then writes. ``if_match`` reads, compares and writes. Limitation: that is not atomic across
machines; the run lease (one writer per run) is what prevents two writers, and the check only catches
mistakes.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from hone_flow.errors import HoneFlowError, WriteConflict
from hone_flow.ports import Entry
from hone_flow.serialize import sha256_bytes


class FsspecStorage:
    """Run storage at an fsspec URL such as ``s3://bucket/prefix`` or ``memory://bucket/proj``.

    ``copy`` is the filesystem's own copy (a server-side CopyObject on S3).
    """

    def __init__(self, url: str, **storage_options: Any) -> None:
        self.url = url.rstrip("/")
        storage_options.setdefault("use_listings_cache", False)  # other processes change runs too
        try:
            fsspec: Any = importlib.import_module("fsspec")  # optional extra, loaded when used
            fs, root = fsspec.core.url_to_fs(self.url, **storage_options)  # s3:// also needs s3fs
        except ImportError as exc:
            raise HoneFlowError(
                f"storage {url!r} needs {exc.name or 'fsspec'}: pip install 'hone-flow[s3]'"
            ) from None
        self.fs: Any = fs
        self.root: str = str(root).rstrip("/")

    def _path(self, key: str) -> str:
        return f"{self.root}/{key}".rstrip("/")

    def read_bytes(self, key: str) -> bytes:
        return bytes(self.fs.cat_file(self._path(key)))

    def write_bytes(
        self, key: str, data: bytes, *, if_match: str | None = None, if_absent: bool = False
    ) -> None:
        path = self._path(key)
        if if_absent:
            self._create(path, key, data)
            return
        if if_match is not None and self._sha256(key) != if_match:
            raise WriteConflict(f"{key} changed since it was read (another writer?)")
        self.fs.pipe_file(path, data)

    def _create(self, path: str, key: str, data: bytes) -> None:
        """Exclusive create where the backend has it (S3 ``If-None-Match``), else check then write."""
        try:
            self.fs.pipe_file(path, data, mode="create")
        except FileExistsError:
            raise WriteConflict(f"{key} already exists") from None
        except (TypeError, NotImplementedError, ValueError):  # a backend without mode="create"
            if self.fs.isfile(path):
                raise WriteConflict(f"{key} already exists") from None
            self.fs.pipe_file(path, data)

    def _sha256(self, key: str) -> str | None:
        try:
            return sha256_bytes(self.read_bytes(key))
        except FileNotFoundError:
            return None

    def upload(self, local_path: Path, key: str) -> None:
        self.fs.put_file(str(local_path), self._path(key))

    def download(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        self.fs.get_file(self._path(key), str(local_path))

    def copy(self, src_key: str, dst_key: str) -> None:
        self.fs.copy(self._path(src_key), self._path(dst_key))

    def list(self, prefix: str) -> list[str]:
        path = self._path(prefix)
        if self.fs.isfile(path):
            return [prefix]
        if not self.fs.exists(path):
            return []
        root = self.root.lstrip("/")  # fs.find drops the leading "/" on some filesystems
        found: list[str] = self.fs.find(path)
        return sorted(p.lstrip("/").removeprefix(root).lstrip("/") for p in found)

    def delete(self, prefix: str) -> None:
        path = self._path(prefix)
        if self.fs.exists(path):
            self.fs.rm(path, recursive=True)

    def exists(self, key: str) -> bool:
        return bool(self.fs.isfile(self._path(key)))

    def list_dir(self, prefix: str) -> list[Entry]:
        """The children of a folder from one listing call (one level), sorted by name; ``[]`` if missing."""
        path = self._path(prefix)
        try:
            listed: list[dict[str, Any]] = self.fs.ls(path, detail=True)
        except FileNotFoundError:
            return []
        base = path.strip("/")
        found: dict[str, Entry] = {}
        for info in listed:
            full = str(info["name"]).strip("/")
            name = full.rsplit("/", 1)[-1]
            if full == base or not name or ".tmp-" in name:
                continue
            is_dir = info.get("type") == "directory"
            found[name] = Entry(name, is_dir, None if is_dir else int(info.get("size") or 0))
        return sorted(found.values(), key=lambda e: e.name)

    def size(self, key: str) -> int:
        info = self.fs.info(self._path(key))
        if info.get("type") == "directory":
            raise FileNotFoundError(key)
        return int(info["size"])

    def read_range(self, key: str, start: int, length: int) -> bytes:
        """A byte range (an HTTP Range request on S3), without downloading the file."""
        if length <= 0:
            return b""
        return bytes(self.fs.cat_file(self._path(key), start=start, end=start + length))
