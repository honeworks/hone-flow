"""GPU batching: neighbouring GPU steps share one lease, so models load once per batch, not per item.

What: steps tagged ``resources="gpu:<name>"`` hold the workflow's ``GpuLease`` while they run. In the
default breadth-first order, a run of neighbouring steps with the same tag takes the lease once for all
items; ``order="depth_first"`` takes it per item. ``fk.testing.FakeGpuLease`` records the leases.

How: tag steps (``@wf.step(resources="gpu:ollama", vram_gb=6)``) and pass ``gpu=`` to the workflow: a
``GpuLease`` object (``fk.FileLockGpuLease()`` for one machine-wide lock, hone-models' GPU scheduler) or
an entry-point name (``gpu="file_lock"``, ``gpu="hone_models"``). ``ctx.gpu_lease()`` re-enters it.

Why: on one 8 GB GPU, loading a 7 GB model per item is the slowest part of a pipeline. Grouping the
Ollama steps, then the ComfyUI steps, turns N model swaps into one.
"""

import tempfile

import hone_flow as fk
from hone_flow.testing import FakeGpuLease

calls: list[str] = []


def pipeline(gpu: fk.GpuLease, order: str = "breadth_first") -> fk.Workflow:
    wf = fk.Workflow("gpu_demo", storage=tempfile.mkdtemp(), gpu=gpu, order=order)

    @wf.step(resources="gpu:ollama", vram_gb=6.0)
    def shotlist(lyrics: str, ctx: fk.Context) -> str:
        calls.append(f"shotlist/{ctx.item_id}")
        return lyrics

    @wf.step(resources="gpu:ollama", vram_gb=6.0)
    def prompts(shotlist: str, ctx: fk.Context) -> str:
        calls.append(f"prompts/{ctx.item_id}")
        return shotlist

    @wf.step(resources="gpu:comfyui", vram_gb=7.5)
    def images(prompts: str, ctx: fk.Context) -> str:
        with ctx.gpu_lease(7.5):  # re-enters the batch's lease (reentrant)
            calls.append(f"images/{ctx.item_id}")
        return prompts

    return wf


items = [fk.Item(f"0{i}", {"lyrics": f"song {i}"}) for i in (1, 2, 3)]
gpu = FakeGpuLease()
pipeline(gpu=gpu).run(items)
print(calls)
print(gpu.calls)
assert gpu.calls == [
    ("enter", "gpu:ollama", 6.0),
    ("exit", "gpu:ollama"),
    ("enter", "gpu:comfyui", 7.5),
    ("exit", "gpu:comfyui"),
]  # two leases for 3 items

calls.clear()
per_item = FakeGpuLease()
pipeline(gpu=per_item, order="depth_first").run(items)
print(calls[:3], "...")
assert len([c for c in per_item.calls if c[0] == "enter"]) == 6  # two per item
