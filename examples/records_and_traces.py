"""Records and traces: every call on a run leaves spans in its folder, linked across packages.

What: hone-flow writes OpenTelemetry-shaped spans to ``<run>/spans.jsonl``: one ``hone.flow.run`` per
run / resume / fork call, one ``hone.flow.step`` (or ``hone.flow.gate``) per step and item it touched.
A step passes ``ctx.current_trace()`` to other packages (hone-models, hone-select) so their spans join
the same trace, under the step's span.

How: nothing to switch on; read them with ``run.spans()`` or any JSONL reader. Pass ``trace=`` to
``wf.run`` to join a caller's trace, and ``Workflow(sink=...)`` to also send spans to another
``RecordSink`` (e.g. ``fk.JsonlSpanSink``, ``fk.SqliteSpanSink``). ``HONE_CAPTURE_CONTENT=0`` stores
hashes instead of params and notes; secrets are always stripped.

Why: one trace id links a workflow step to the model calls it made, so tools like hone-lens can explain
a bad output from its run folder alone.
"""

import json
import secrets
import tempfile
from pathlib import Path

import hone_flow as fk


def other_package_call(prompt: str, *, trace: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Stands in for e.g. ``hone_models.text(...).complete(..., trace=...)``, which records its own span."""
    _, trace_id, parent_span, _ = trace["traceparent"].split("-")  # W3C traceparent
    span = {
        "name": "hone.models.chat",
        "trace_id": trace_id,
        "parent_span_id": parent_span,
        "span_id": secrets.token_hex(8),
    }
    return prompt.upper(), span


model_spans: list[dict[str, str]] = []
tmp = Path(tempfile.mkdtemp(prefix="hone-flow-example-"))
wf = fk.Workflow("trace_demo", storage=tmp / "flows", sink=fk.JsonlSpanSink(tmp / "all-spans.jsonl"))


@wf.step()
def answer(question: str, ctx: fk.Context) -> str:
    text, span = other_package_call(question, trace=ctx.current_trace())
    model_spans.append(span)
    return text


caller = {"traceparent": "00-" + "4bf92f3577b34da6a3ce929d0e0e4736" + "-" + "00f067aa0ba902b7" + "-01"}
run = wf.run([fk.Item("q1", {"question": "why is the sky blue?"})], trace=caller)
spans = run.spans()
for span in spans:
    print(span["name"], span["attributes"].get("hone.step", ""), span["attributes"]["hone.flow.status"])
run_span = next(s for s in spans if s["name"] == "hone.flow.run")
step_span = next(s for s in spans if s["name"] == "hone.flow.step")

assert run_span["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"  # joined the caller's trace
assert step_span["parent_span_id"] == run_span["span_id"]
assert model_spans[0]["trace_id"] == run_span["trace_id"]  # the other package joined it too ...
assert model_spans[0]["parent_span_id"] == step_span["span_id"]  # ... under the step
sink_lines = (tmp / "all-spans.jsonl").read_text().splitlines()
assert [json.loads(line)["span_id"] for line in sink_lines] == [s["span_id"] for s in spans]
