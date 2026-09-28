"""``MemoryStorage``: a ``RunStorage`` in a dict, with exact semantics, for tests."""

from __future__ import annotations

from pathlib import Path

from hone_flow.errors import WriteConflict
from hone_flow.ports import Entry
from hone_flow.serialize import sha256_bytes


class MemoryStorage:
    """Run storage in memory (``self.files``: key -> bytes). Not shared between processes.

    >>> storage = MemoryStorage()
    >>> storage.write_bytes("w/a.json", b"{}")
    >>> storage.read_bytes("w/a.json")
    b'{}'
    """

    def __init__(self) -> None:
        self.url = "memory"
        self.files: dict[str, bytes] = {}

    def read_bytes(self, key: str) -> bytes:
        if key not in self.files:
            raise FileNotFoundError(key)
        return self.files[key]

    def write_bytes(
        self, key: str, data: bytes, *, if_match: str | None = None, if_absent: bool = False
    ) -> None:
        if if_absent and key in self.files:
            raise WriteConflict(f"{key} already exists")
        if if_match is not None and (key not in self.files or sha256_bytes(self.files[key]) != if_match):
            raise WriteConflict(f"{key} changed since it was read (another writer?)")
        self.files[key] = bytes(data)

    def upload(self, local_path: Path, key: str) -> None:
        self.files[key] = local_path.read_bytes()

    def download(self, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.read_bytes(key))

    def copy(self, src_key: str, dst_key: str) -> None:
        self.files[dst_key] = self.read_bytes(src_key)

    def list(self, prefix: str) -> list[str]:
        folder = prefix.rstrip("/") + "/" if prefix else ""
        return sorted(k for k in self.files if k == prefix or k.startswith(folder))

    def delete(self, prefix: str) -> None:
        for key in self.list(prefix):
            del self.files[key]

    def exists(self, key: str) -> bool:
        return key in self.files

    def list_dir(self, prefix: str) -> list[Entry]:
        base = prefix.rstrip("/") + "/" if prefix else ""
        found: dict[str, Entry] = {}
        for key, data in self.files.items():
            if key.startswith(base):
                name, _, below = key.removeprefix(base).partition("/")
                found[name] = Entry(name, True) if below else Entry(name, False, len(data))
        return sorted(found.values(), key=lambda e: e.name)

    def size(self, key: str) -> int:
        return len(self.read_bytes(key))

    def read_range(self, key: str, start: int, length: int) -> bytes:
        return self.read_bytes(key)[start : start + max(length, 0)]
