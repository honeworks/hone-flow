"""AC-30: progress while a run runs (design change 0003): on_event callbacks and INFO log lines."""

import logging
from pathlib import Path
from typing import Any

import pytest

import hone_flow as fk
from hone_flow.testing import FakeGpuLease

pytestmark = pytest.mark.e2e


def make(storage: Path, fail: set[str]) -> tuple[fk.Workflow, list[str]]:
    wf = fk.Workflow("progress", storage=storage, gpu=FakeGpuLease())
    seen_during: list[str] = []

    @wf.step(resources="gpu:tts", vram_gb=4.0)
    def speak(text: str, ctx: fk.Context) -> str:
        seen_during.append(ctx.run_id)
        if text in fail:
            raise ValueError(f"cannot say {text}")
        return text.upper()

    @wf.step()
    def publish(speak: str) -> str:
        return f"<{speak}>"

    return wf, seen_during


def test_ac30_events_in_order(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    wf, seen_during = make(tmp_path / "flows", fail={"b"})
    events: list[dict[str, Any]] = []

    def on_event(event: dict[str, Any]) -> None:
        if event["event"] == "step_started" and event["step"] == "speak":
            # the run id and location are known before any step runs: the folder can be inspected
            assert Path(events[0]["location"], "manifest.json").is_file()
        events.append(event)

    with caplog.at_level(logging.INFO, logger="hone_flow"):
        run = wf.run([fk.Item("a", {"text": "a"}), fk.Item("b", {"text": "b"})], on_event=on_event)

    names = [(e["event"], e.get("step"), e.get("item")) for e in events]
    assert names == [
        ("run_started", None, None),
        ("lease_waiting", None, None),
        ("lease_granted", None, None),
        ("step_started", "speak", "a"),
        ("step_finished", "speak", "a"),
        ("step_started", "speak", "b"),
        ("step_finished", "speak", "b"),
        ("step_started", "publish", "a"),
        ("step_finished", "publish", "a"),
        ("run_finished", None, None),
    ]
    first = events[0]
    assert (first["run_id"], first["workflow"], first["kind"]) == (run.run_id, "progress", "run")
    assert first["location"] == run.location
    assert seen_during == [run.run_id, run.run_id]
    assert (events[1]["name"], events[1]["vram_gb"]) == ("gpu:tts", 4.0)
    assert isinstance(events[2]["wait_ms"], int)
    finished = {(e["step"], e["item"]): e for e in events if e["event"] == "step_finished"}
    assert finished[("speak", "b")]["status"] == "failed"
    assert finished[("speak", "a")]["status"] == "done"
    assert finished[("speak", "a")]["attempt"] == 1
    assert isinstance(finished[("speak", "a")]["duration_ms"], int)
    assert events[-1]["status"] == "failed"
    assert all(e["at"] for e in events)

    text = caplog.text
    assert f"run {run.run_id} of progress (run) at {run.location}" in text
    assert "step speak/a started (attempt 1)" in text
    assert "step speak/b failed in" in text
    assert "waiting for GPU lease 'gpu:tts' (4.0 GB)" in text
    assert f"run {run.run_id} of progress failed" in text


def test_ac30_resume_fork_and_a_failing_callback(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    wf, _ = make(tmp_path / "flows", fail={"b"})
    run = wf.run([fk.Item("a", {"text": "a"}), fk.Item("b", {"text": "b"})])
    wf2, _ = make(tmp_path / "flows", fail=set())

    resumed: list[dict[str, Any]] = []
    wf2.open_run(run.run_id).resume(on_event=resumed.append)
    assert resumed[0]["kind"] == "resume"
    assert [(e["step"], e["item"], e["attempt"]) for e in resumed if e["event"] == "step_started"] == [
        ("speak", "b", 2),
        ("publish", "b", 1),
    ]
    assert resumed[-1] == resumed[-1] | {"event": "run_finished", "status": "completed"}

    def broken(event: dict[str, Any]) -> None:
        raise RuntimeError("display crashed")

    with caplog.at_level(logging.WARNING, logger="hone_flow"):
        forked = wf2.open_run(run.run_id).fork(refresh=("publish",), on_event=broken)
    assert forked.status == "completed"  # a failing callback never breaks the run
    assert "the on_event callback failed" in caplog.text

    kinds: list[dict[str, Any]] = []
    wf2.open_run(run.run_id).fork(refresh=("publish",), on_event=kinds.append)
    assert kinds[0]["kind"] == "fork"
    assert kinds[0]["run_id"] != run.run_id
