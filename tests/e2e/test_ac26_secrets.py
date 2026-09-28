"""AC-26: secret-looking values never reach the run folder or the records."""

from pathlib import Path

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e

SECRET = "sk-live1234567890abcdef"  # noqa: S105 - a planted fake key


def test_ac26_secrets(tmp_path: Path) -> None:
    sink = fk.MemorySink()
    wf = fk.Workflow("secrets", storage=tmp_path, sink=sink)

    @wf.step()
    def call_api(prompt: str, api_key: fk.Param[str]) -> str:
        raise PermissionError(f"key {api_key} was refused")

    run = wf.run([fk.Item("01", {"prompt": "hi"})], params={"api_key": SECRET})
    assert run.status == "failed"
    record = run.steps("call_api", "01")[0]
    assert record.params == {"api_key": "***"}
    assert record.error is not None
    assert "***" in record.error["message"]
    assert SECRET not in record.error["traceback"]
    assert run.manifest["params"] == {"api_key": "***"}
    for path in Path(run.location).rglob("*"):
        if path.is_file():
            assert SECRET not in path.read_text(), path  # manifest, metadata, reports, spans
    assert all(SECRET not in str(span) for span in sink.spans)
