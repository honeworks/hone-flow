"""AC-7: resume never mixes step versions."""

import logging
from pathlib import Path

import pytest

import hone_flow as fk

pytestmark = pytest.mark.e2e


def flow(
    storage: Path,
    calls: list[str],
    *,
    v_a: str = "1",
    v_b: str = "1",
    version: str = "1",
    b_suffix: str = "!",
    extra_step: bool = False,
    fail_b: bool = True,
) -> fk.Workflow:
    wf = fk.Workflow("versions", storage=storage, version=version)

    @wf.step(version=v_a)
    def a(text: str) -> str:
        calls.append("a")
        return text

    if b_suffix == "!":

        @wf.step(version=v_b)
        def b(a: str) -> str:
            calls.append("b")
            if fail_b:
                raise RuntimeError("b is broken")
            return a + "!"
    else:

        @wf.step(version=v_b)
        def b(a: str) -> str:  # a source edit
            calls.append("b")
            return a + "?"

    if extra_step:

        @wf.step()
        def c(b: str) -> str:
            return b

    return wf


def failed_run(tmp_path: Path) -> str:
    return flow(tmp_path, []).run([fk.Item("01", {"text": "x"})]).run_id


def test_ac7_incompatible_resume_version_bump_of_remaining_work(tmp_path: Path) -> None:
    run_id = failed_run(tmp_path)
    calls: list[str] = []
    run = flow(tmp_path, calls, v_b="2", fail_b=False).open_run(run_id)
    with pytest.raises(
        fk.IncompatibleRun, match=r"step 'b' version 1 -> 2.*fork the run instead: run\.fork\(\)"
    ):
        run.resume()
    assert calls == []  # nothing executed
    assert run.status == "failed"
    assert [c["kind"] for c in run.manifest["calls"]] == ["run"]
    assert not (Path(run.location) / "lease.json").exists()


def test_ac7_incompatible_resume_changed_inputs_and_removed_steps(tmp_path: Path) -> None:
    run_id = failed_run(tmp_path)
    wf = fk.Workflow("versions", storage=tmp_path)

    @wf.step()
    def a(text: str) -> str:
        return text

    @wf.step()
    def b(a: str, text: str) -> str:  # takes one more input
        return a + text

    with pytest.raises(fk.IncompatibleRun, match="step 'b' changed its inputs or outputs"):
        wf.open_run(run_id).resume()
    only_a = fk.Workflow("versions", storage=tmp_path)
    only_a.step()(a)
    with pytest.raises(fk.IncompatibleRun, match=r"removed \['b'\]"):
        only_a.open_run(run_id).resume()


def test_ac7_incompatible_resume_skipped_step_counts_as_remaining_work(tmp_path: Path) -> None:
    run_id = flow(tmp_path, [], fail_b=False).run([fk.Item("01", {"text": "x"})], until="a").run_id
    with pytest.raises(fk.IncompatibleRun, match="step 'b' version 1 -> 2"):
        flow(tmp_path, [], v_b="2", fail_b=False).open_run(run_id).resume()


@pytest.mark.parametrize(
    ("change", "message"),
    [({"version": "2"}, "workflow version 1 -> 2"), ({"extra_step": True}, r"steps changed \(added \['c'\]")],
)
def test_ac7_incompatible_resume_workflow_changes(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    run_id = failed_run(tmp_path)
    calls: list[str] = []
    with pytest.raises(fk.IncompatibleRun, match=message):
        flow(tmp_path, calls, fail_b=False, **change).open_run(run_id).resume()  # type: ignore[arg-type]
    assert calls == []


def test_ac7_incompatible_resume_done_step_version_bump_proceeds(tmp_path: Path) -> None:
    run_id = failed_run(tmp_path)
    calls: list[str] = []
    run = flow(tmp_path, calls, v_a="2", fail_b=False).open_run(run_id)
    run.resume()
    assert calls == ["b"]  # a is done: never recomputed by resume
    assert run.status == "completed"


def test_ac7_incompatible_resume_source_change_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run_id = failed_run(tmp_path)
    run = flow(tmp_path, [], b_suffix="?").open_run(run_id)
    with caplog.at_level(logging.WARNING, logger="hone_flow"):
        run.resume()
    assert run.output("b", "01") == "x?"
    assert "step 'b' changed its source without a version bump" in caplog.text
    (warning,) = run.manifest["warnings"]
    assert warning["kind"] == "source_changed_version_unchanged"
    assert warning["step"] == "b"
    assert warning["old"] != warning["new"]
    resume_span = [s for s in run.spans() if s["name"] == "hone.flow.run"][-1]
    (event,) = resume_span["events"]
    assert event["name"] == "warning"
    assert event["attributes"]["kind"] == "source_changed_version_unchanged"
    assert event["attributes"]["step"] == "b"


def test_ac7_incompatible_resume_done_step_source_change_is_silent(tmp_path: Path) -> None:
    run_id = flow(tmp_path, [], fail_b=False).run([fk.Item("01", {"text": "x"})]).run_id
    run = flow(tmp_path, [], b_suffix="?").open_run(run_id)
    run.resume()
    assert run.manifest["warnings"] == []
