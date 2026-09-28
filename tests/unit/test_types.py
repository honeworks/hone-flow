import logging
import typing
from pathlib import Path

import hone_flow as fk
from hone_flow.testing import FakeGpuLease


def make_context(tmp_path: Path, gpu: FakeGpuLease, resources: str = "cpu") -> fk.Context:
    return fk.Context(
        run_id="r1",
        trace_id="t" * 32,
        item_id="01",
        step="render",
        attempt=1,
        seed=42,
        logger=logging.getLogger("test"),
        workdir=tmp_path / "work",
        trace={"traceparent": "00-" + "t" * 32 + "-" + "s" * 16 + "-01"},
        gpu=gpu,
        resources=resources,
    )


def test_context_makes_files_and_folders_in_its_work_folder(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, FakeGpuLease())
    path = ctx.new_file("sub/clip.mp4")
    assert path == tmp_path / "work" / "sub" / "clip.mp4"
    assert path.parent.is_dir()
    folder = ctx.new_dir("frames")
    assert folder.is_dir()


def test_context_trace_is_a_copy(tmp_path: Path) -> None:
    ctx = make_context(tmp_path, FakeGpuLease())
    trace = ctx.current_trace()
    trace["x"] = "y"
    assert "x" not in ctx.current_trace()


def test_gpu_lease_defaults_to_the_step_tag_or_step_name(tmp_path: Path) -> None:
    gpu = FakeGpuLease()
    with make_context(tmp_path, gpu, resources="gpu:ollama").gpu_lease(4.0):
        pass
    with make_context(tmp_path, gpu).gpu_lease(), make_context(tmp_path, gpu).gpu_lease(name="custom"):
        pass
    entered = [c for c in gpu.calls if c[0] == "enter"]
    assert entered == [
        ("enter", "gpu:ollama", 4.0),
        ("enter", "hone-flow:render", 0.0),
        ("enter", "custom", 0.0),
    ]
    assert gpu.traces[0] == make_context(tmp_path, gpu).trace


def test_param_item_file_and_dir() -> None:
    assert fk.File("a/b.txt").path == Path("a/b.txt")
    assert fk.Dir("frames").path == Path("frames")
    assert fk.Item("01").inputs == {}
    assert repr(typing.get_args(fk.Param[int])[1]) == "hone_flow.Param"


def test_step_failed_carries_step_item_run_and_traceback() -> None:
    err = fk.StepFailed("render", "02", "Traceback ...\nValueError: boom", run_id="r1")
    assert (err.step, err.item, err.run_id) == ("render", "02", "r1")
    assert err.traceback.endswith("ValueError: boom")
    assert "step 'render' failed for item '02' in run 'r1'" in str(err)
    assert isinstance(err, fk.HoneFlowError)
