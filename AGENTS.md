# AGENTS.md: hone-flow

A brief for AI coding tools (Claude Code, Codex, Cursor and others) helping with this repository.
People follow the same rules; they are in [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

`hone-flow` (import `hone_flow`) runs AI workflows of plain-function steps as self-contained run folders,
with resume, fork, partial runs and human gates. Read [design/current.md](design/current.md) before
changing behaviour; it is the design as it stands, and its §7 lists the guarantees the tests check. Why
the design looks like this: [design/changes/](design/changes/) and [design/decisions.md](design/decisions.md).

## Commands

```bash
uv sync --all-extras                 # install everything (dev included)
scripts/check.sh                     # all quality gates: lint, format, types, tests + coverage, build, wheel smoke test
uv run pytest                        # default suite (fast, offline, deterministic)
uv run pytest tests/e2e              # acceptance cases only
scripts/gpu-lock.sh uv run pytest -m gpu   # real-model tests, under the machine-wide GPU lock
uv run ruff check . && uv run ruff format .
uv run pyright                       # strict for src/
```

## Layout

```text
src/hone_flow/
  workflow.py          Workflow, decorators, validation, run / open_run / runs / cleanup
  dag.py  types.py  serialize.py  invoke.py  inputs.py   DAG inference, user types, serializers, calling steps, input snapshots
  storage.py           LocalStorage, open_storage; fsspec_storage.py: FsspecStorage (extra s3)
  run_format.py        manifest / metadata / lease models (format_version "1", docs/run-format.md)
  run_lease.py         run lease: acquire, heartbeat, takeover
  commit.py            attempt commit (metadata.json last), retire, cleanup
  executor.py          order, GPU batching, failure isolation
  schedule.py  resume.py  produced.py        per-unit decisions, resume checks, items made by a step
  run.py  fork.py  reviews.py  history.py   Run, fork diff, gates, listings / pin / cleanup
  notifications.py     Slack / Mattermost / Discord / HTTP webhooks, delivery states
  metrics.py  reports.py  spans.py          measurements, reports/, spans.jsonl
  progress.py          progress events: INFO logs and the on_event callback
  leases.py            GpuLease implementations; ports.py: the Protocols
  adapters/            optional integrations, imported lazily
  testing/             MemoryStorage, FakeGpuLease, WebhookServer, contract checkers
  _tracing.py  _records.py                  trace context, span sinks
tests/unit|contract|integration|e2e|gpu
docs/                  user docs; every Python block is run by tests/e2e/test_docs.py
examples/              one explained, runnable example per concept (tests/e2e/test_examples.py)
design/                why and how: README, current.md, changes/, decisions.md, history/
```

Run folders under `<storage>/<name>/runs/<run_id>/` are the only state: there is no database and no
cache.

## Rules

1. **Keep it simple**: the simplest code that passes the acceptance cases; no speculative abstractions;
   complexity at most 10 per function, about 40 lines per function and 300 per module.
2. **The core is useful alone**: it never imports another honeworks package or an optional extra at import
   time; integrations go through the ports in `ports.py` and lazily imported adapters.
3. **Explicit failure**: typed errors (`HoneFlowError` subclasses) with messages that say what to do.
4. **Run folders**: self-contained (copies, not references); `metadata.json` written last; manifest writes
   conditional and under the run lease; the format is versioned and documented in `docs/run-format.md`.
5. **Records and secrets**: spans as in `docs/records.md`, in each run's `spans.jsonl`; never record
   secrets or webhook URLs anywhere.
6. **Determinism**: explicit seeds; `hashlib`, never `hash()`, for ids.
7. **Tests first** for public behaviour; each acceptance case has a `tests/e2e/test_ac<N>_*` test; docs and
   examples are executed by tests. Green means `scripts/check.sh` passes; never weaken a test to pass.
8. **Real models** only through `scripts/gpu-lock.sh`; unload what you load.
9. **Design changes** get a record in `design/changes/` first; small choices go into
   `design/decisions.md`; update `design/current.md` when behaviour changes.
10. **Git**: Conventional Commits, small commits; AI-written commits end with a `Co-Authored-By:` line.
    Do not push, tag or publish unless the maintainer asks.
