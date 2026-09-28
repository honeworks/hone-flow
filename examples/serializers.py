"""Serializers: how step outputs are stored, named and loaded back.

What: JSON values and Pydantic models are stored as ``<output>.json``; any other type needs
``fk.register_serializer(cls, dump, load, extension=...)`` and is stored as ``<output>.<extension>``.

How: return JSON-compatible data or a Pydantic model and it just works; for your own class, register
``dump(value) -> bytes`` and ``load(bytes) -> value`` once at import time. ``run.output`` and the next
step both get the value back (the Pydantic model itself when its class can be imported, else a dict).

Why: outputs are readable files with extensions, so a person can open ``output/timeline.json`` or
``output/ratio.frac`` in any tool, and the metadata records the type (``pydantic:<module>:<class>``,
``custom:<name>``) so the value can be rebuilt.
"""

import fractions
import json
import tempfile
from pathlib import Path

from pydantic import BaseModel

import hone_flow as fk


class Timeline(BaseModel):
    duration: float
    lines: list[str]


fk.register_serializer(
    fractions.Fraction,
    dump=lambda value: str(value).encode(),
    load=lambda data: fractions.Fraction(data.decode()),
    name="fraction",
    extension="frac",
)

wf = fk.Workflow("serializers_demo", storage=tempfile.mkdtemp())


@wf.step()
def timeline(text: str) -> Timeline:
    lines = text.splitlines()
    return Timeline(duration=4.0 * len(lines), lines=lines)


@wf.step()
def stats(timeline: Timeline) -> dict[str, object]:  # receives the model, not a dict
    return {"lines": len(timeline.lines), "longest": max(timeline.lines, key=len)}


@wf.step()
def ratio(timeline: Timeline) -> fractions.Fraction:
    return fractions.Fraction(len(timeline.lines), 4)


run = wf.run([fk.Item("01", {"text": "one\ntwo words\nthree"})])
folder = Path(run.location)
print(run.output("timeline", "01"))
print((folder / "ratio/item_01/output/ratio.frac").read_text())
print(json.loads((folder / "timeline/item_01/metadata.json").read_text())["outputs"]["timeline"]["type"])

assert run.output("timeline", "01") == Timeline(duration=12.0, lines=["one", "two words", "three"])
assert run.output("stats", "01") == {"lines": 3, "longest": "two words"}
assert run.output("ratio", "01") == fractions.Fraction(3, 4)
assert (folder / "stats/item_01/output/stats.json").is_file()
assert run.steps("ratio", "01")[0].outputs["ratio"]["type"] == "custom:fraction"
