"""AC-29 [real, comfyui, optional]: a step renders a small image through ComfyUI via the `comfy` CLI.

Needs: `comfy` on PATH, the ComfyUI workspace ($HONE_TEST_COMFYUI_WORKSPACE, default ~/ComfyUI), a
running server ($HONE_TEST_COMFYUI_URL, default http://127.0.0.1:8188) and an API-format workflow JSON
for z_image_turbo ($HONE_TEST_COMFYUI_WORKFLOW). Skipped with the reason when any is missing.
"""

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

import pytest

import hone_flow as fk

pytestmark = [pytest.mark.gpu, pytest.mark.comfyui]

WORKSPACE = Path(os.environ.get("HONE_TEST_COMFYUI_WORKSPACE", "~/ComfyUI")).expanduser()
URL = os.environ.get("HONE_TEST_COMFYUI_URL", "http://127.0.0.1:8188")


def comfy_ready() -> tuple[str, Path]:
    comfy = shutil.which("comfy")
    if comfy is None:
        pytest.skip("the `comfy` CLI is not on PATH")
    if not WORKSPACE.is_dir():
        pytest.skip(f"ComfyUI workspace {WORKSPACE} does not exist")
    try:
        with urllib.request.urlopen(f"{URL}/system_stats", timeout=3):  # noqa: S310
            pass
    except OSError:
        pytest.skip(f"ComfyUI server not running at {URL}")
    workflow = os.environ.get("HONE_TEST_COMFYUI_WORKFLOW")
    if not workflow or not Path(workflow).is_file():
        pytest.skip("set HONE_TEST_COMFYUI_WORKFLOW to an API-format z_image_turbo workflow JSON")
    return comfy, Path(workflow)


def test_ac29_comfyui_render(gpu_lock: None, tmp_path: Path) -> None:
    comfy, workflow = comfy_ready()
    wf = fk.Workflow("comfy_real", storage=tmp_path / "flows", gpu=fk.FileLockGpuLease(tmp_path / "gpu.lock"))

    @wf.step(resources="gpu:comfyui", vram_gb=7, deterministic=False)
    def render(prompt: str, ctx: fk.Context) -> fk.File:
        output_dir = WORKSPACE / "output"
        before = set(output_dir.glob("*.png"))
        command = [
            comfy,
            "--workspace",
            str(WORKSPACE),
            "run",
            "--workflow",
            str(workflow),
            "--wait",
            "--timeout",
            "600",
        ]
        subprocess.run(command, check=True)
        new = sorted(set(output_dir.glob("*.png")) - before, key=lambda p: p.stat().st_mtime)
        assert new, "ComfyUI produced no image"
        target = ctx.new_file("lighthouse.png")
        shutil.copyfile(new[-1], target)
        return fk.File(target)

    run = wf.run([fk.Item("1", {"prompt": "a lighthouse at dusk"})])
    assert run.status == "completed", [r.error for r in run.steps() if r.error]
    image = run.output("render", "1")
    assert image.path.name == "lighthouse.png"  # the file keeps its own name in output/
    assert image.path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (Path(run.location) / "render/item_1/output/lighthouse.png").is_file()
