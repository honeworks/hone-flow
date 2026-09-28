"""Turning step outputs into bytes and back, and sha256 helpers.

JSON-compatible values and Pydantic models become canonical JSON (sorted keys, UTF-8); other types go
through serializers registered with ``register_serializer``. ``File`` and ``Dir`` values are files and
folders, not bytes, so they are not handled here.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hone_flow.errors import HoneFlowError
from hone_flow.types import Item, Items

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class _Serializer:
    name: str
    cls: type
    dump: Callable[[Any], bytes]
    load: Callable[[bytes], Any]
    extension: str


_SERIALIZERS: dict[str, _Serializer] = {}


def register_serializer(
    cls: type,
    dump: Callable[[Any], bytes],
    load: Callable[[bytes], Any],
    *,
    name: str | None = None,
    extension: str = "bin",
) -> None:
    """Store values of ``cls`` with your own ``dump`` / ``load`` functions, as ``output/<name>.<extension>``.

    >>> import fractions
    >>> register_serializer(fractions.Fraction, lambda f: str(f).encode(),
    ...                     lambda b: fractions.Fraction(b.decode()), extension="txt")
    """
    key = name or _type_path(cls)
    _SERIALIZERS[key] = _Serializer(key, cls, dump, load, extension.lstrip("."))


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, UTF-8, two-space indent (readable in a run folder)."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False).encode()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Streaming sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _type_path(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


def import_type(path: str) -> Any:
    module_name, _, qualname = path.partition(":")
    obj: Any = sys.modules.get(module_name) or importlib.import_module(module_name)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def _custom_for(value: Any) -> _Serializer | None:
    return next((s for s in _SERIALIZERS.values() if isinstance(value, s.cls)), None)


def dump_value(value: Any) -> tuple[bytes, str, str]:
    """(bytes, output type, file extension) of a value that is not an ``fk.File`` / ``fk.Dir``.

    Output types: ``json``, ``pydantic:<module>:<qualname>``, ``custom:<serializer name>``.
    """
    if isinstance(value, BaseModel):
        return canonical_json(value.model_dump(mode="json")), f"pydantic:{_type_path(type(value))}", "json"
    custom = _custom_for(value)
    if custom is not None:
        return custom.dump(value), f"custom:{custom.name}", custom.extension
    try:
        return canonical_json(value), "json", "json"
    except (TypeError, ValueError) as exc:
        raise HoneFlowError(
            f"cannot serialize a value of type {type(value).__name__}: {exc}. Return JSON-compatible data, "
            "a Pydantic model, fk.File / fk.Dir, or register a serializer with fk.register_serializer"
        ) from None


def load_value(data: bytes, type_: str) -> Any:
    """Inverse of ``dump_value``. A Pydantic model whose class cannot be imported loads as a dict."""
    kind, _, name = type_.partition(":")
    if kind == "json":
        return json.loads(data)
    if kind == "pydantic":
        try:
            model = import_type(name)
        except (ImportError, AttributeError):
            return json.loads(data)
        return model.model_validate_json(data)
    if kind == "custom":
        if name not in _SERIALIZERS:
            raise HoneFlowError(f"no serializer registered under {name!r}; call fk.register_serializer first")
        return _SERIALIZERS[name].load(data)
    raise HoneFlowError(f"unknown output type {type_!r}")


def _dump_items(items: Items) -> bytes:
    return canonical_json([{"id": item.id, "inputs": dict(item.inputs)} for item in items])


def _load_items(data: bytes) -> Items:
    return Items(Item(entry["id"], entry["inputs"]) for entry in json.loads(data))


register_serializer(Items, _dump_items, _load_items, name="items", extension="json")  # design §4.20
