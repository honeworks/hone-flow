---
name: add-adapter
description: Add an implementation of one of hone-flow's ports - a RunStorage backend, a GpuLease, a RecordSink - or another optional integration, with its extra, fake, contract test and docs. Use when adding support for a new storage system, lock, sink or third-party library.
---

# Add an adapter

The ports are in `src/hone_flow/ports.py`: `RunStorage` (`design/current.md` §4.2), `GpuLease`,
`RecordSink`. A new port, or a change to one, is a design change: `plan-change` first.

1. **Where.** Storage backends are modules next to `src/hone_flow/storage.py` (like
   `src/hone_flow/fsspec_storage.py`) and are chosen by URL in `open_storage`. Integrations with other
   packages go in `src/hone_flow/adapters/`.
2. **Optional dependency.** Add an extra in `pyproject.toml`. Import the third-party library only inside
   the adapter module, and import that module lazily (see `open_storage`). The core must still import
   without it: `tests/unit/test_import_boundaries.py`.
3. **Contract.** Run the checker from `hone_flow.testing.contracts` (`check_run_storage`,
   `check_gpu_lease`, `check_record_sink`) against the adapter in `tests/contract/`.
4. **Guarantees the adapter must keep.** Storage: conditional writes (`if_match`, `if_absent`) raise
   `WriteConflict` when the condition fails; the run lease and commit protocol depend on it
   (`design/current.md` §4.4). If the backend can't do this atomically, say so in the change record and
   fail clearly instead of pretending.
5. **Tests against a real local service**, never a remote one, in `tests/integration/` (S3 uses a local
   moto server). Crash and two-process tests for storage.
6. **Entry point** when the port is found by name (`hone.gpu_leases` in `pyproject.toml`).
7. `sync-docs`: the install line, `docs/storage.md` or `docs/adapters.md`, an example if it's a new kind of
   backend.
