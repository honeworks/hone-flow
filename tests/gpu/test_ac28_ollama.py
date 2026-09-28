"""AC-28 [real]: a step calls Ollama through plain httpx under a gpu:ollama FileLockGpuLease.

It runs and commits, resumes after a planted failure, a fork(refresh=...) gives a new sample, and
reused steps are labeled reused. The model is unloaded afterwards (the ``ollama_model`` fixture).
Run through the machine-wide lock: ``scripts/gpu-lock.sh uv run pytest -m gpu``.
"""

import os
from pathlib import Path
from typing import Any

import httpx
import pytest

import hone_flow as fk

pytestmark = [pytest.mark.gpu, pytest.mark.ollama]

OLLAMA_URL = os.environ.get("HONE_TEST_OLLAMA_URL", "http://127.0.0.1:11434")


def make(model: str, storage: Path, lock: Path, broken: dict[str, bool]) -> tuple[fk.Workflow, list[str]]:
    calls: list[str] = []
    wf = fk.Workflow("ollama_real", storage=storage, gpu=fk.FileLockGpuLease(lock))

    @wf.step(resources="gpu:ollama", vram_gb=8, deterministic=False)
    def tagline(topic: str, ctx: fk.Context) -> str:
        calls.append(topic)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": f"Write one short tagline (under 12 words) about {topic}. Reply with the tagline only.",
            "stream": False,
            "think": False,
            "options": {"seed": ctx.seed, "num_predict": 40, "num_ctx": 1024, "temperature": 0.9},
        }
        response = httpx.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=600)
        response.raise_for_status()
        return str(response.json()["response"]).strip()

    @wf.step()
    def shout(tagline: str, ctx: fk.Context) -> str:
        if broken.get(ctx.item_id):
            raise RuntimeError("planted failure")
        return tagline.upper()

    return wf, calls


def test_ac28_ollama_step(ollama_model: Any, tmp_path: Path) -> None:
    model = ollama_model("HONE_TEST_TEXT_MODEL", "gemma4-12b:latest")
    items = [fk.Item("rain", {"topic": "rain"}), fk.Item("sun", {"topic": "sunshine"})]
    broken = {"sun": True}
    wf, calls = make(model, tmp_path / "flows", tmp_path / "gpu.lock", broken)

    run = wf.run(items)
    assert run.status == "failed"  # the planted failure, after the model calls were committed
    assert sorted(calls) == ["rain", "sunshine"]
    first = run.output("tagline", "rain")
    assert isinstance(first, str)
    assert first.strip()
    assert run.output("shout", "rain") == first.upper()
    assert run.steps("tagline", "sun")[0].status == "done"

    broken["sun"] = False
    calls.clear()
    run.resume()
    assert run.status == "completed"
    assert calls == []  # resume reran only the failed step, not the model call
    assert run.steps("shout", "sun")[0].attempt == 2

    fork = run.fork(refresh=("tagline",), items=["rain"])
    assert calls == ["rain"]
    assert fork.status == "completed"
    assert fork.steps("tagline", "rain")[0].labels == []
    assert fork.steps("tagline", "rain")[0].reused_from is None
    new_sample = fork.output("tagline", "rain")
    assert isinstance(new_sample, str)
    assert new_sample.strip()
    reused = run.fork(refresh=("shout",), items=["rain"])
    assert reused.steps("tagline", "rain")[0].labels == ["reused"]  # a non-deterministic output, copied
    assert reused.output("tagline", "rain") == first
    spans = [s for s in fork.spans() if s["attributes"].get("hone.step") == "tagline"]
    assert spans[0]["attributes"]["hone.flow.resource"] == "gpu:ollama"
