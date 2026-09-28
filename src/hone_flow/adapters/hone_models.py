"""GPU leases from entry points: ``Workflow(gpu="hone_models")`` uses hone-models' GPU scheduler.

Any package can offer a ``GpuLease`` under the entry-point group ``hone.gpu_leases``; hone-models
registers ``hone_models``, hone-flow itself registers ``file_lock`` (``FileLockGpuLease``).
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, cast

from hone_flow.errors import HoneFlowError
from hone_flow.ports import GpuLease

GROUP = "hone.gpu_leases"


def load_gpu_lease(name: str) -> GpuLease:
    """Load the ``GpuLease`` registered as ``name``; a registered class is instantiated without arguments.

    >>> load_gpu_lease("file_lock").__class__.__name__
    'FileLockGpuLease'
    """
    found = {ep.name: ep for ep in entry_points(group=GROUP)}
    if name not in found:
        hint = " (pip install hone-models)" if name == "hone_models" else ""
        raise HoneFlowError(
            f"no GPU lease named {name!r} in entry points {GROUP}; found {sorted(found)}{hint}"
        )
    obj: Any = found[name].load()
    lease = obj() if isinstance(obj, type) else obj
    if not callable(getattr(lease, "lease", None)):
        raise HoneFlowError(f"entry point {name!r} in {GROUP} is not a GpuLease (it has no lease() method)")
    return cast(GpuLease, lease)
