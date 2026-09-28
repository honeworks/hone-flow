# Contributing to hone-flow

Thank you for helping. This page covers how to set up, the rules the code follows, how it is tested and
how the design changes. The design itself is in [`design/`](design/README.md); start with
[`design/current.md`](design/current.md).

## Setup and the quality gates

```bash
uv sync --all-extras          # install the package with every extra and the dev tools
scripts/check.sh              # every quality gate; "green" means this exits 0
```

`scripts/check.sh` runs ruff (lint and format), pyright (strict for `src/`), the default test suite with
branch coverage of at least 90% on `src/`, `uv build`, and a smoke test that installs the built wheel
alone in a fresh virtual environment and runs the README quickstart. Pull requests must keep it green.
Never weaken a test to make it pass.

Other useful commands:

```bash
uv run pytest                          # default suite: fast, offline, deterministic
uv run pytest tests/e2e                # the acceptance cases only
uv run ruff check . && uv run ruff format .
uv run pyright
scripts/gpu-lock.sh uv run pytest -m gpu   # real-model tests (see below)
```

## Keep it simple

The first rule: **simple, easy to understand, maintainable and expandable.** When simplicity conflicts
with anything except correctness and the guarantees in [`design/current.md`](design/current.md#7-guarantees-acceptance-cases),
simplicity wins. A newcomer should be able to open any module, understand what it does in a few minutes
and change it safely.

1. Build the simplest thing that passes the acceptance cases, not the most general thing.
2. No speculative generality. Add an abstraction only when there are two real uses today, or the design
   names it as an extension point (a port, a registry, an entry point).
3. Extend through a few clear seams: the ports (`GpuLease`, `RecordSink`, `RunStorage`), the
   `hone.gpu_leases` entry points, `fk.register_serializer`, and plain functions users pass in. Everything
   else is ordinary concrete code.
4. Plain Python first: functions and small dataclasses before classes, composition before inheritance,
   the standard library before dependencies.
5. Flat and explicit: short call chains, no metaclasses, import hooks, monkey-patching or deep decorator
   stacks.
6. One obvious way to configure and call each thing (for hone-flow: `Workflow(...)` arguments).
7. Names over comments; comments say *why*.
8. Delete freely: dead code, unused parameters and single-caller helper layers go.

Limits, checked in review (ruff enforces complexity):

| Thing | Limit |
|---|---|
| Cyclomatic complexity per function | 10 (`C901`) |
| Function length | about 40 lines |
| Module length | about 300 lines |
| Parameters per function | 6; group more into a small dataclass (the `Workflow` constructor is the one configuration point and the exception) |
| Inheritance depth of own classes | 1 |
| Variants | a dict of functions or a `match`, not a class hierarchy |
| Core dependencies | stdlib + pydantic; anything else is an optional extra, and a new one needs a written reason in `design/decisions.md` |

Avoid "manager / handler / processor" layers, base classes with one subclass, plugin systems beyond the
entry points, event buses and dependency-injection containers.

## Code conventions

- Python 3.11 or newer; ruff with line length 110; pyright strict for `src/`; the public API is fully
  typed and `py.typed` is shipped.
- The public API is exported from `src/hone_flow/__init__.py` with an explicit `__all__`; public functions
  and classes have docstrings.
- **The core is useful alone.** It never imports another honeworks package or an optional extra at import
  time; adapters live in `src/hone_flow/adapters/` and are imported lazily.
  `tests/unit/test_import_boundaries.py` checks this.
- **Ports and adapters.** A port is a small `typing.Protocol` owned by hone-flow (`ports.py`); adapters
  are injected as constructor arguments or found through entry points; every port has a fake in
  `hone_flow.testing` and a contract checker in `hone_flow.testing.contracts`.
- **Explicit failure.** Never swallow errors silently. Errors crossing the public API are typed
  (`HoneFlowError` and its subclasses in `errors.py`), and messages say what happened and what to do.
- **Run folders.** Every run folder is self-contained (copies, not references); `metadata.json` is written
  last; manifest writes are conditional and happen under the run lease; the format is versioned
  (`format_version`) and documented in [`docs/run-format.md`](docs/run-format.md). Adding optional keys
  keeps the version; removing a key or changing its meaning needs a new version and a design change.
- **Records.** Spans follow [`docs/records.md`](docs/records.md) and go to each run's `spans.jsonl`.
  Secrets (and webhook URLs) are never recorded in spans, manifests, metadata, reports or logs.
- **Determinism.** Seeds are explicit; use `hashlib`, never the built-in `hash()`, for ids.
- Time is `datetime.now(UTC)`, stored as ISO-8601 UTC; paths are `pathlib.Path`; logging goes to
  `logging.getLogger("hone_flow")`, never `print` in library code.
- Secrets come only from environment variables.

## Tests

| Suite | Folder | Runs by default | Purpose |
|---|---|---|---|
| Unit | `tests/unit/` | yes | each module's behaviour, edge cases and errors, with fakes |
| Contract | `tests/contract/` | yes | the ports' implementations and fakes pass the contract checkers |
| Integration | `tests/integration/` | yes | modules together with real local resources: the filesystem, subprocesses, a local S3 server (moto) |
| Acceptance | `tests/e2e/` | yes | the numbered guarantees of [`design/current.md`](design/current.md#7-guarantees-acceptance-cases), through the public API and CLI only |
| Real model | `tests/gpu/` | no | the same cases against real models on a local GPU |

- The default suite must stay fast, deterministic and offline.
- Every acceptance case has a test in `tests/e2e/` named `test_ac<N>_<slug>`, using only the public API or
  CLI. An acceptance test must fail if its feature is removed.
- The README quickstart, every Python block in `docs/` and every file in `examples/` are run by the tests.
  Each example opens with a What / How / Why docstring and is listed in `examples/README.md`.
- Test crash safety with a subprocess that is killed mid-write, and concurrency with two processes on one
  run folder.
- Check records: span names and attributes, trace links across nested calls, the content-capture switch,
  and that a planted fake secret appears nowhere.

### Real-model tests and `scripts/gpu-lock.sh`

Real-model tests (`-m gpu`, plus `ollama` / `comfyui`) run only through `scripts/gpu-lock.sh`, which holds
one machine-wide lock (`$HONE_GPU_LOCK`, default `/tmp/honeworks-gpu.lock`) so that test runs from several
projects never share a small GPU at once. A session fixture in `tests/conftest.py` takes the same lock if
you run pytest directly. The tests check that Ollama or ComfyUI is up and skip with a reason when it is
not, and unload the models they loaded. Models are chosen with `HONE_TEST_OLLAMA_URL` and
`HONE_TEST_TEXT_MODEL`.

## Changing the design

Design changes are written down before they are built:

1. Write `design/changes/NNNN-<short-name>.md` with status `proposed` and the sections Status, Context,
   Problem, Options, Decision, Consequences, and Migration and compatibility.
2. The maintainer reviews it; the status becomes `accepted` (or `rejected`).
3. Implement it from [`design/current.md`](design/current.md) and the accepted change records, tests
   first.
4. Update `design/current.md`, set the record to `implemented in <version>`, and add a `CHANGELOG.md`
   entry that links to it.

Smaller implementation choices that need no change record go into
[`design/decisions.md`](design/decisions.md).

## Commits

- Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `build:`, `ci:`), a
  subject of at most 72 characters, a body that explains why; one logical change per commit.
- Commits written with an AI tool end with a `Co-Authored-By:` line naming it.
- Never commit secrets, `.hone/`, model weights or large binaries.
- `CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Contributors using AI coding tools will find a short brief for them in [`AGENTS.md`](AGENTS.md).
