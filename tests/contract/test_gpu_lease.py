import threading
from importlib.metadata import entry_points
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.adapters.hone_models import load_gpu_lease
from hone_flow.testing import FakeGpuLease, MemoryStorage
from hone_flow.testing.contracts import check_gpu_lease


def test_fake_gpu_lease_contract() -> None:
    fake = FakeGpuLease()
    check_gpu_lease(fake)
    assert fake.calls == [("enter", "contract-test", 0.1), ("exit", "contract-test")]


def test_null_and_file_lock_leases_pass_the_contract(tmp_path: Path) -> None:
    check_gpu_lease(fk.NullGpuLease())
    check_gpu_lease(fk.FileLockGpuLease(tmp_path / "gpu.lock"))


def test_file_lock_is_exclusive_and_times_out(tmp_path: Path) -> None:
    lock = tmp_path / "gpu.lock"
    held, release = threading.Event(), threading.Event()

    def other_holder() -> None:
        with fk.FileLockGpuLease(lock).lease("other", 1.0):
            held.set()
            release.wait(5)

    thread = threading.Thread(target=other_holder)
    thread.start()
    held.wait(5)
    try:
        with (
            pytest.raises(TimeoutError, match="still locked"),
            fk.FileLockGpuLease(lock).lease("x", 1.0, timeout_s=0.2),
        ):
            pass
    finally:
        release.set()
        thread.join()
    with fk.FileLockGpuLease(lock).lease("x", 1.0, timeout_s=1):  # free again
        pass


def test_leases_resolve_through_entry_points(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HONE_GPU_LEASE_LOCK", str(tmp_path / "gpu.lock"))
    lease = load_gpu_lease("file_lock")
    assert isinstance(lease, fk.FileLockGpuLease)
    assert lease.path == tmp_path / "gpu.lock"
    assert isinstance(fk.Workflow("w", storage=MemoryStorage(), gpu="file_lock").gpu, fk.FileLockGpuLease)
    with pytest.raises(fk.HoneFlowError, match=r"no GPU lease named 'nope' .* \['file_lock'"):
        load_gpu_lease("nope")
    if "hone_models" not in {ep.name for ep in entry_points(group="hone.gpu_leases")}:
        with pytest.raises(fk.HoneFlowError, match="pip install hone-models"):
            load_gpu_lease("hone_models")
