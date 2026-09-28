# Command line

The `hone-flow` command (extra `cli`: `pip install "hone-flow[cli]"`) runs, resumes, forks, inspects and
reviews run folders from a shell. `python -m hone_flow.cli` is the same command.

Example: [`cli_session.py`](../examples/cli_session.py).

## Finding the workflow and its runs

- `--flow module:attribute` imports the workflow, e.g. `--flow myflows:wf` for `wf` in `myflows.py`. The
  current folder is importable. `run`, `resume` and `fork` need it: they execute the workflow's code.
- The read and review commands (`status`, `show`, `runs`, `approve`, `edit`, `reject`, `pin`, `cleanup`,
  `notify-retry`) also accept `--storage URL --name NAME` instead: the storage root and the workflow name.
  They need no workflow code, so a reviewer's machine only needs hone-flow and access to the storage.

## A session

The blocks on this page are one shell session (the test suite runs them). First a workflow module and an
items file:

```bash
cat > myflows.py <<'EOF'
import hone_flow as fk

wf = fk.Workflow("notes", storage="flows")


@wf.step()
def draft(topic: str, tone: fk.Param[str]) -> str:
    return f"a {tone} note about {topic}"


@wf.gate()
def review(draft: str) -> str:
    return draft


@wf.step()
def send(review: str) -> str:
    return review.upper()
EOF

cat > items.json <<'EOF'
[{"id": "01", "inputs": {"topic": "tides"}},
 {"id": "02", "inputs": {"topic": "moons"}}]
EOF
```

Start a run. It stops at the gate (`awaiting_review`, exit code 0):

```bash
hone-flow run --flow myflows:wf --items items.json --param tone=calm
RUN_ID=$(hone-flow runs --storage flows --name notes --json \
         | python -c 'import json, sys; print(json.load(sys.stdin)[0]["run_id"])')
hone-flow status "$RUN_ID" --storage flows --name notes
```

Review without the workflow's code, then continue with it:

```bash
hone-flow approve "$RUN_ID" --step review --item 01 --storage flows --name notes
hone-flow reject "$RUN_ID" --step review --item 02 --note "more precise" --storage flows --name notes
hone-flow resume "$RUN_ID" --flow myflows:wf        # 01 is sent; 02's draft reruns, the gate pauses again
EDITOR="perl -pi -e s/calm/warm/" \
  hone-flow edit "$RUN_ID" --step review --item 02 --storage flows --name notes
hone-flow resume "$RUN_ID" --flow myflows:wf --json
hone-flow show "$RUN_ID" --step review --item 02 --storage flows --name notes
```

Fork, list, pin, clean up:

```bash
hone-flow fork "$RUN_ID" --flow myflows:wf --param tone=urgent --dry-run
hone-flow fork "$RUN_ID" --flow myflows:wf --refresh draft --items 01
hone-flow runs --storage flows --name notes --updated-since 2026-01-01T00:00:00Z
hone-flow pin "$RUN_ID" --storage flows --name notes
hone-flow cleanup --storage flows --name notes --keep-last 1 --dry-run
hone-flow notify-retry "$RUN_ID" --storage flows --name notes
hone-flow status no-such-run --storage flows --name notes || echo "exit code $?"
```

The last command prints `error: no run 'no-such-run' of workflow 'notes' in ...` on stderr and exits 1.

## Commands

Every command has `--json` for machine-readable output (secret-stripped, like all output).

| Command | Options | Does |
|---|---|---|
| `run` | `--flow` (required), `--items FILE` (required), `--param name=value` (repeatable), `--until STEP`, `--items-filter 01,03`, `--seed N` (default 0), `--label TEXT`, `--description TEXT` | start a new run |
| `resume RUN_ID` | `--flow` (required), `--until STEP`, `--items 01,03` | continue the run: pending, failed, interrupted, blocked and skipped work |
| `fork RUN_ID` | `--flow` (required), `--refresh STEP` (repeatable), `--items 01,03`, `--param name=value` (repeatable), `--dry-run`, `--label TEXT`, `--description TEXT` | a new run from this one; `--dry-run` prints the plan and writes nothing |
| `status RUN_ID` | `--flow` or `--storage/--name` | status, location and the number of steps per state |
| `show RUN_ID` | `--step STEP` (required), `--item ID`, `--flow` or `--storage/--name` | the step records (status, attempts, reviews, inputs, outputs, error, timings), always as JSON |
| `runs` | `--flow` or `--storage/--name`, `--updated-since ISO-TIME` | the runs, newest first, with their labels |
| `label RUN_ID TEXT` | `--description TEXT`, `--flow` or `--storage/--name` | name a run (an empty `TEXT` removes the label) |
| `approve RUN_ID` | `--step GATE` (required), `--item ID`, `--note TEXT`, `--automated`, `--flow` or `--storage/--name` | approve a gate's output |
| `edit RUN_ID` | `--step GATE` (required), `--item ID`, `--flow` or `--storage/--name` | open the gate's output as JSON in `$EDITOR`; the saved JSON becomes the output |
| `reject RUN_ID` | `--step GATE` (required), `--note TEXT` (required), `--item ID`, `--automated`, `--flow` or `--storage/--name` | reject; resume reruns the producers with the note as `review_note` |
| `pin RUN_ID` | `--flow` or `--storage/--name` | copy a finished run to `pinned_runs/` |
| `cleanup` | `--flow` or `--storage/--name`, `--keep-last N`, `--older-than AGE` (`30d`, `12h`, `45m`), `--dry-run` | delete old run folders under `runs/` (never pinned copies or runs in use) |
| `notify-retry RUN_ID` | `--flow` or `--storage/--name` | send pending and failed notification deliveries again |

- `--param` values are JSON when they parse as JSON (`--param size=512`, `--param 'tags=["a","b"]'`),
  text otherwise (`--param tone=calm`).
- `edit` runs `$EDITOR` with the path of a temporary JSON file (the variable may include arguments, as
  above). A failing editor or invalid JSON changes nothing and exits 1.
- `--json` output: `run`, `resume`, `fork` and `status` print `{"run_id", "workflow", "status",
  "location", "states": {state: count}, "pinned"}`; `fork --dry-run` prints `{"source_run_id", "rows":
  [{"item", "step", "action", "reason"}]}`; `runs` a list of [`RunSummary`](read-api.md#runsummary)
  objects; `show` a list of [`StepRecord`](read-api.md#steprecord) objects; `approve`, `edit` and
  `reject` `{"run_id", "step", "item", "decision"}`; `pin` `{"run_id", "pinned": true}`; `cleanup`
  `{"deleted", "kept", "locked"}`; `label` `{"run_id", "label", "description"}`; `notify-retry` a list of `{"event_id", "event", "destination",
  "state", "attempts", "error"}`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | success, including a run that stops at a gate (`awaiting_review`) or is `partial` |
| 1 | failure: a run whose status is `failed` (`run`, `resume`, `fork`, `status`), or an error printed as one line `error: ...` on stderr (a library error, a storage error, a failing editor, a user module that fails to import) |
| 2 | usage error: unknown command or option, a bad `--param`, `--updated-since` or `--older-than`, an items file that cannot be read, a module that does not exist, neither `--flow` nor `--storage/--name` |

## The items file

`--items` takes a JSON list of items. Each has an `id` and `inputs`; file and folder inputs are
`{"$file": path}` and `{"$dir": path}`, relative to the items file:

```json
[
  {"id": "01", "inputs": {"lyrics": {"$file": "songs/01.md"}, "mood": "calm"}},
  {"id": "02", "inputs": {"lyrics": {"$file": "songs/02.md"}, "frames": {"$dir": "frames/02"}}}
]
```
