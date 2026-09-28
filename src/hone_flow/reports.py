"""Run reports (design §4.10): ``reports/*.json`` for the enabled measurements and ``reports/summary.md``.

Reports are for people and are rewritten at the end of every call; they are not part of the run
format (``docs/run-format.md``). Everything in them comes from the step metadata and the manifest.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from hone_flow.metrics import SYSTEM_MEASURES
from hone_flow.run_format import Manifest, RunFolder, StepMetadata, split_state_key
from hone_flow.serialize import canonical_json

TOP = 5  # rows in each "top" table of summary.md


def write_reports(folder: RunFolder, manifest: Manifest) -> None:
    """(Re)write the reports of a run from its current metadata."""
    metas = [m for key in manifest.state if (m := folder.read_meta(*split_state_key(key))) is not None]
    measure = set(manifest.measure)
    if "timing" in measure:
        rows = [_row(m) | m.measurements.get("timing", {}) for m in metas if "timing" in m.measurements]
        _write(folder, "timing.json", {"steps": rows, "total_ms": sum(r["duration_ms"] for r in rows)})
    if "output_sizes" in measure:
        rows = [_row(m) | {"bytes": _bytes(m), "files": _files(m)} for m in metas if m.outputs]
        _write(folder, "output_sizes.json", {"steps": rows, "total_bytes": sum(_bytes(m) for m in metas)})
    if measure & set(SYSTEM_MEASURES):
        measured = [m for m in metas if "system" in m.measurements]
        rows = [_row(m) | m.measurements["system"] for m in measured]
        _write(
            folder,
            "system_resources.json",
            {"steps": rows, "peaks": _peaks([m.measurements["system"] for m in measured])},
        )
    folder.storage.write_bytes(folder.key("reports", "summary.md"), summary(manifest, metas).encode())


def summary(manifest: Manifest, metas: list[StepMetadata]) -> str:
    """A short Markdown summary: status, states per step, slowest steps, largest outputs, peaks, warnings."""
    lines = [f"# {manifest.workflow} run {manifest.run_id}", "", f"Status: **{manifest.status}**", ""]
    counts = Counter(manifest.state.values())
    lines += ["## Steps", "", *[f"- {state}: {n}" for state, n in sorted(counts.items())], ""]
    timed = sorted(
        (m for m in metas if m.duration_ms is not None), key=lambda m: m.duration_ms or 0, reverse=True
    )
    if "timing" in manifest.measure and timed:
        lines += ["## Slowest steps", "", *[f"- {_name(m)}: {m.duration_ms} ms" for m in timed[:TOP]], ""]
    sized = sorted((m for m in metas if m.outputs), key=_bytes, reverse=True)
    if "output_sizes" in manifest.measure and sized:
        lines += ["## Largest outputs", "", *[f"- {_name(m)}: {_bytes(m)} bytes" for m in sized[:TOP]], ""]
    peaks = _peaks([m.measurements["system"] for m in metas if "system" in m.measurements])
    if peaks:
        lines += ["## Resource peaks", "", *[f"- {key}: {value:.2f}" for key, value in peaks.items()], ""]
    if "gpu" in manifest.measure and not any(key.startswith("hone.flow.gpu.") for key in peaks):
        lines += ["GPU measurements are left out: no GPU was visible.", ""]
    if manifest.warnings:
        lines += [
            "## Warnings",
            "",
            *[f"- {w.get('kind')}: step {w.get('step')}" for w in manifest.warnings],
            "",
        ]
    return "\n".join(lines)


def _write(folder: RunFolder, name: str, data: dict[str, Any]) -> None:
    folder.storage.write_bytes(folder.key("reports", name), canonical_json(data))


def _row(meta: StepMetadata) -> dict[str, Any]:
    return {"step": meta.step, "item": meta.item, "status": meta.status, "attempt": meta.attempt}


def _name(meta: StepMetadata) -> str:
    return meta.step if meta.item is None else f"{meta.step}/{meta.item}"


def _bytes(meta: StepMetadata) -> int:
    return sum(f.size for o in meta.outputs.values() for f in o.files.values())


def _files(meta: StepMetadata) -> dict[str, int]:
    return {name: f.size for o in meta.outputs.values() for name, f in o.files.items()}


def _peaks(rows: list[dict[str, Any]]) -> dict[str, float]:
    """The largest value of every numeric metric over the steps."""
    peaks: dict[str, float] = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, int | float):
                peaks[key] = max(peaks.get(key, value), value)
    return peaks
