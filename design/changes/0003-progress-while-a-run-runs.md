# 0003: Progress while a run runs

## Status

`implemented in 0.1.0` (approved by the owner 2026-09-28)

## Context

Found while building OneShotStudio (a demo app built on the honeworks packages). One `oneshot make "<prompt>"` is one
`wf.run(...)` call of 11 steps that takes 40-60 minutes on one GPU (LLM selections, several song
renders, 50 keyframes). The command line tool wanted to print where the run lives and which step is
running, so a person (or a log) can follow it and inspect the run folder while it runs.

`wf.run(...)` returns only when the call ends, so the run id and location are not known to the caller
until then. hone-flow logs only warnings. The app ended up finding "the newest `manifest.json` under
`runs/oneshot/runs/`" and polling its `state` table from a second process. That works, but it is a guess
(two runs started at once break it) and every application will write the same loop. When a GPU lease
waited forever (see hone-models change 0004), nothing showed that the run was stuck between two steps.

## Problem

- The caller cannot learn the run id / location before the call returns.
- There is no in-process way to observe step starts and ends (for a progress line, a UI, a timeout).
- Nothing is logged at INFO level, so plain `logging.basicConfig(level=logging.INFO)` shows nothing.

## Options

1. **INFO logs only**: `hone_flow` logs "run <id> at <location>", "step <s>/<item> started / done in
   <t> s / failed", "waiting for GPU lease <name>". Zero API change; not structured.
2. **A callback**: `wf.run(..., on_event=callable)` receiving small dicts (`run_started`, `step_started`,
   `step_finished`, `lease_waiting`, `lease_granted`, `run_finished`).
3. **Start without waiting**: `wf.start(items, ...) -> Run` that creates the run folder and returns, plus
   `run.wait()`; progress is read from `run.status` / `run.steps()`.
4. Rely on the `sink=` record sink: spans are emitted when a step *ends*, so they cannot show a step that
   is running or a lease that is waiting.

## Decision

Proposed: 1 and 2. INFO logs cover command-line tools with no code; the callback covers UIs and tests
and is the same set of events the logs print. Option 3 changes the execution model (threads or a second
process) and is not needed for progress.

## Consequences

- Applications print progress and the run location immediately, with no polling of run folders.
- A stuck lease becomes visible as `lease_waiting` without a matching `lease_granted`.
- A callback that raises must not break the run: exceptions are logged and ignored, like notifications.

## Migration and compatibility

Additive: a new optional keyword on `run`, `resume` and `fork`, and INFO log lines on the `hone_flow`
logger (silent unless the application configures logging).

## Implementation notes

- `hone_flow/progress.py`: one `report()` function logs the event and passes a copy to `on_event`.
  Events: `run_started`, `step_started`, `step_finished`, `lease_waiting`, `lease_granted`,
  `run_finished`; each carries `event`, `run_id`, `workflow`, `at` (design §4.17).
- Units that do not run (reused by a fork, skipped, blocked) send no event: the spans record them.
- AC-30 in `design/current.md` §7; `examples/progress.py`; docs in `docs/concepts.md`.
