import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

import hone_flow as fk
from hone_flow.serialize import canonical_json, dump_value, load_value, sha256_bytes, sha256_file


class Shot(BaseModel):
    line: str
    seconds: float


class Point:
    def __init__(self, x: int) -> None:
        self.x = x


fk.register_serializer(
    Point, lambda p: str(p.x).encode(), lambda b: Point(int(b)), name="point", extension=".pt"
)

json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=10,
)


@given(json_values)
def test_json_round_trip_is_canonical(value: object) -> None:
    data, type_, extension = dump_value(value)
    assert (type_, extension) == ("json", "json")
    assert data == canonical_json(value)
    assert canonical_json(load_value(data, type_)) == data


def test_distinct_values_have_distinct_bytes() -> None:
    values = [None, 0, False, "", 0.0, [], {}]
    assert len({dump_value(v)[0] for v in values}) == len(values)


def test_canonical_json_exact_bytes() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == '{\n  "a": "é",\n  "b": 1\n}'.encode()


def test_canonical_json_sorts_keys() -> None:
    assert canonical_json({"b": 1, "a": "é"}) == canonical_json({"a": "é", "b": 1})
    assert "é" in canonical_json({"a": "é"}).decode()


def test_pydantic_round_trip() -> None:
    data, type_, extension = dump_value(Shot(line="hi", seconds=1.5))
    assert type_ == f"pydantic:{__name__}:Shot"
    assert extension == "json"
    assert json.loads(data) == {"line": "hi", "seconds": 1.5}
    assert load_value(data, type_) == Shot(line="hi", seconds=1.5)
    assert load_value(data, "pydantic:gone.module:Shot") == {
        "line": "hi",
        "seconds": 1.5,
    }  # class gone: a dict


def test_custom_serializer() -> None:
    data, type_, extension = dump_value(Point(7))
    assert (data, type_, extension) == (b"7", "custom:point", "pt")
    assert load_value(data, type_).x == 7


def test_unknown_custom_serializer_and_kind() -> None:
    with pytest.raises(fk.HoneFlowError, match="no serializer registered"):
        load_value(b"1", "custom:nope")
    with pytest.raises(fk.HoneFlowError, match="unknown output type"):
        load_value(b"1", "weird")


def test_unserializable_value_says_what_to_do() -> None:
    with pytest.raises(fk.HoneFlowError, match="register a serializer"):
        dump_value(object())
    with pytest.raises(fk.HoneFlowError, match="cannot serialize"):
        dump_value(float("nan"))


def test_sha256_of_a_file_streams_and_matches_the_bytes(tmp_path: Path) -> None:
    path = tmp_path / "big.bin"
    data = b"frames" * 400_000  # larger than one read chunk
    path.write_bytes(data)
    assert sha256_file(path) == sha256_bytes(data)
