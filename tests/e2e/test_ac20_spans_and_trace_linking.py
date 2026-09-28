"""AC-20: spans.jsonl records every call, with trace links across packages."""

import json
import secrets
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e

CALLER = {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01", "hone.lens.finding_id": "F-7"}


class FakeModels:
    """Another package that records a span in the caller's trace (as hone-models does)."""

    def __init__(self) -> None:
        self.spans: list[dict[str, Any]] = []

    def chat(self, prompt: str, *, trace: dict[str, str]) -> str:
        _, trace_id, parent, _ = trace["traceparent"].split("-")  # W3C: 00-<trace id>-<parent span>-01
        span = {
            "name": "hone.models.chat",
            "trace_id": trace_id,
            "parent_span_id": parent,
            "span_id": secrets.token_hex(8),
            "attributes": {"hone.step": trace.get("hone.step")},
        }
        self.spans.append(span)
        return prompt.upper()


def build(storage: Path, models: FakeModels, sink: fk.MemorySink, broken: dict[str, bool]) -> fk.Workflow:
    wf = fk.Workflow("traced", storage=storage, sink=sink)

    @wf.step()
    def draft(text: str, ctx: fk.Context) -> str:
        if broken.get(ctx.item_id):
            raise RuntimeError("sk-abcdefghijklmnop leaked in a message")
        return models.chat(text, trace=ctx.current_trace())

    @wf.step()
    def polish(draft: str) -> str:
        return draft + "!"

    @wf.gate()
    def check(polish: str) -> str:
        return polish

    return wf


def test_ac20_spans_and_trace_linking(tmp_path: Path) -> None:
    models, sink, broken = FakeModels(), fk.MemorySink(), {"02": True}
    wf = build(tmp_path, models, sink, broken)
    run = wf.run(
        [fk.Item("01", {"text": "a"}), fk.Item("02", {"text": "b"}), fk.Item("03", {"text": "c"})],
        items_filter=["01", "02"],
        trace=CALLER,
        params={"token": "Bearer abc.def"},
    )
    spans = run.spans()
    by_name = {s["name"] for s in spans}
    assert by_name == {"hone.flow.run", "hone.flow.step", "hone.flow.gate"}
    statuses = {
        (s["attributes"]["hone.step"], s["attributes"].get("hone.item")): s["attributes"]["hone.flow.status"]
        for s in spans
        if s["name"] != "hone.flow.run"
    }
    assert statuses[("draft", "02")] == "failed"
    assert statuses[("polish", "02")] == "blocked"
    assert statuses[("draft", "03")] == "skipped"
    assert statuses[("check", "01")] == "awaiting_review"
    (run_span,) = [s for s in spans if s["name"] == "hone.flow.run"]
    assert run_span["trace_id"] == "a" * 32  # the caller's trace is joined
    assert run_span["parent_span_id"] == "b" * 16
    assert run_span["attributes"]["hone.lens.finding_id"] == "F-7"  # caller keys on every span
    assert all(s["attributes"]["hone.lens.finding_id"] == "F-7" for s in spans)
    assert all(not k.startswith("hone.flow.cache") for s in spans for k in s["attributes"])
    assert "***" in run_span["attributes"]["hone.flow.params"]
    (draft_01,) = [
        s
        for s in spans
        if s["attributes"].get("hone.item") == "01" and s["attributes"]["hone.step"] == "draft"
    ]
    (nested,) = models.spans
    assert nested["trace_id"] == "a" * 32  # the nested package span shares the trace ...
    assert nested["parent_span_id"] == draft_01["span_id"]  # ... under the step's span
    assert draft_01["parent_span_id"] == run_span["span_id"]
    assert draft_01["attributes"]["hone.flow.attempt"] == 1
    assert json.loads(draft_01["attributes"]["hone.flow.outputs"])["draft"]
    assert [s["span_id"] for s in sink.spans] == [s["span_id"] for s in spans]  # sink= gets the same spans

    broken["02"] = False
    run.resume(items=["01", "02"])
    retried = [
        s
        for s in run.spans()
        if s["attributes"].get("hone.item") == "02" and s["attributes"].get("hone.step") == "draft"
    ]
    assert [s["attributes"]["hone.flow.attempt"] for s in retried] == [1, 2]
    runs = [s for s in run.spans() if s["name"] == "hone.flow.run"]
    assert len(runs) == 2
    assert {s["trace_id"] for s in runs} == {"a" * 32}  # resume continues the trace

    run.approve(step="check", item="01", note="ok sk-abcdefghijklmnop", actor="ana")
    (decision,) = [s for s in run.spans() if s["attributes"].get("hone.flow.gate.decision") == "approved"]
    assert decision["name"] == "hone.flow.gate"
    assert decision["trace_id"] == "a" * 32
    assert decision["attributes"]["hone.flow.status"] == "done"
    assert decision["attributes"]["hone.flow.gate.actor"] == "ana"
    assert decision["attributes"]["hone.flow.gate.note"] == "ok ***"  # secrets stripped in spans ...
    assert run.steps("check", "01")[0].reviews[0]["note"] == "ok ***"  # ... and in metadata

    fork = run.fork(refresh=("polish",))
    fork_spans = fork.spans()
    (fork_run,) = [s for s in fork_spans if s["name"] == "hone.flow.run"]
    assert fork_run["attributes"]["hone.flow.fork_of"] == run.run_id
    reused = [s for s in fork_spans if s["attributes"].get("hone.flow.reused_from") == run.run_id]
    assert {s["attributes"]["hone.step"] for s in reused} == {"draft"}
    text = (Path(run.location) / "spans.jsonl").read_text()
    assert "sk-abcdefghijklmnop" not in text


def test_ac20_spans_and_trace_linking_capture_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HONE_CAPTURE_CONTENT", "0")
    wf = build(tmp_path, FakeModels(), fk.MemorySink(), {})
    run = wf.run([fk.Item("01", {"text": "a"})], params={"style": "private words"})
    (run_span,) = [s for s in run.spans() if s["name"] == "hone.flow.run"]
    captured = json.loads(run_span["attributes"]["hone.flow.params"])
    assert set(captured) == {"sha256", "len"}  # content capture off: hashes, not content
    assert "private words" not in (Path(run.location) / "spans.jsonl").read_text()
    assert fk.current_trace() == {}  # nothing leaks out of the steps


class BrokenSink:
    def emit(self, span: object) -> None:
        raise OSError("sink down")

    def flush(self) -> None:
        raise OSError("sink down")

    def close(self) -> None:
        """Nothing to close."""


def test_ac20_spans_and_trace_linking_a_broken_sink_never_fails_the_run(tmp_path: Path) -> None:
    wf = fk.Workflow("sinky", storage=tmp_path, sink=BrokenSink())

    @wf.step()
    def one(text: str) -> str:
        return text

    run = wf.run([fk.Item("01", {"text": "a"})])
    assert run.status == "completed"
    assert len(run.spans()) == 2  # spans.jsonl still has everything
