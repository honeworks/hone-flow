"""AC-23: GPU lease batching and execution order."""

from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.testing import FakeGpuLease

pytestmark = pytest.mark.e2e


def pipeline(storage: Path, calls: list[str], **options: object) -> fk.Workflow:
    wf = fk.Workflow("clips", storage=storage, **options)  # type: ignore[arg-type]

    @wf.step()
    def lyrics(ctx: fk.Context) -> str:
        calls.append(f"lyrics/{ctx.item_id}")
        return "la"

    @wf.step(resources="gpu:ollama", vram_gb=6.0)
    def shotlist(lyrics: str, ctx: fk.Context) -> str:
        calls.append(f"shotlist/{ctx.item_id}")
        return lyrics

    @wf.step(resources="gpu:ollama", vram_gb=8.0)
    def prompts(shotlist: str, ctx: fk.Context) -> str:
        with ctx.gpu_lease(4.0):  # re-enters the batch's lease
            calls.append(f"prompts/{ctx.item_id}")
        return shotlist

    @wf.step(resources="gpu:comfyui", vram_gb=7.5)
    def images(prompts: str, ctx: fk.Context) -> str:
        calls.append(f"images/{ctx.item_id}")
        return prompts

    @wf.step()
    def assemble(images: str, ctx: fk.Context) -> str:
        calls.append(f"assemble/{ctx.item_id}")
        return images

    return wf


def test_ac23_gpu_batching_and_order_breadth_first(tmp_path: Path) -> None:
    calls: list[str] = []
    gpu = FakeGpuLease()
    run = pipeline(tmp_path, calls, gpu=gpu).run([fk.Item("01"), fk.Item("02")])
    assert run.status == "completed"
    assert calls == [
        "lyrics/01",
        "lyrics/02",
        "shotlist/01",
        "shotlist/02",
        "prompts/01",
        "prompts/02",
        "images/01",
        "images/02",
        "assemble/01",
        "assemble/02",
    ]
    # one lease per batch of neighbouring steps with the same tag, not per item or step
    assert gpu.calls == [
        ("enter", "gpu:ollama", 8.0),
        ("exit", "gpu:ollama"),
        ("enter", "gpu:comfyui", 7.5),
        ("exit", "gpu:comfyui"),
    ]
    assert all(
        t is not None and t["traceparent"].split("-")[1] == run.manifest["trace_id"] for t in gpu.traces
    )


def test_ac23_gpu_batching_and_order_depth_first(tmp_path: Path) -> None:
    calls: list[str] = []
    gpu = FakeGpuLease()
    pipeline(tmp_path, calls, gpu=gpu, order="depth_first").run([fk.Item("01"), fk.Item("02")])
    assert calls == [
        "lyrics/01",
        "shotlist/01",
        "prompts/01",
        "images/01",
        "assemble/01",
        "lyrics/02",
        "shotlist/02",
        "prompts/02",
        "images/02",
        "assemble/02",
    ]
    assert [c for c in gpu.calls if c[0] == "enter"] == [
        ("enter", "gpu:ollama", 8.0),
        ("enter", "gpu:comfyui", 7.5),
    ] * 2


def test_ac23_gpu_batching_and_order_no_lease_when_nothing_runs(tmp_path: Path) -> None:
    gpu = FakeGpuLease()
    wf = pipeline(tmp_path, [], gpu=gpu)
    wf.run([fk.Item("01")], until="lyrics")
    assert gpu.calls == []  # the GPU batch had nothing to run


def test_ac23_gpu_batching_and_order_file_lock_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HONE_GPU_LEASE_LOCK", str(tmp_path / "gpu.lock"))
    wf = pipeline(tmp_path, [], gpu="file_lock")
    assert isinstance(wf.gpu, fk.FileLockGpuLease)
    assert wf.run([fk.Item("01")]).status == "completed"
    with pytest.raises(fk.WorkflowDefinitionError, match="order must be"):
        fk.Workflow("x", storage=tmp_path, order="random")


def test_ac23_gpu_batching_and_order_same_tag_apart_is_two_batches(tmp_path: Path) -> None:
    gpu = FakeGpuLease()
    wf = fk.Workflow("apart", storage=tmp_path, gpu=gpu)

    @wf.step(resources="gpu:ollama", vram_gb=2.0)
    def first() -> int:
        return 1

    @wf.step()
    def middle(first: int) -> int:
        return first

    @wf.step(resources="gpu:ollama", vram_gb=3.0)
    def last(middle: int) -> int:
        return middle

    wf.run([fk.Item("01")])
    assert gpu.calls == [
        ("enter", "gpu:ollama", 2.0),
        ("exit", "gpu:ollama"),
        ("enter", "gpu:ollama", 3.0),
        ("exit", "gpu:ollama"),
    ]


class BrokenLease:
    def lease(self, name: str, vram_gb: float, **kwargs: object) -> object:
        raise TimeoutError("GPU busy")


def test_ac23_gpu_batching_and_order_lease_failure(tmp_path: Path) -> None:
    calls: list[str] = []
    wf = pipeline(tmp_path, calls, gpu=BrokenLease())
    with pytest.raises(
        fk.HoneFlowError, match=r"could not take the GPU lease 'gpu:ollama' \(8.0 GB\): GPU busy"
    ):
        wf.run([fk.Item("01")])
    assert calls == ["lyrics/01"]  # nothing in the GPU batch ran
    (run_folder,) = (tmp_path / "clips" / "runs").iterdir()
    assert not (run_folder / "lease.json").exists()
    assert wf.open_run(run_folder.name).status == "partial"
