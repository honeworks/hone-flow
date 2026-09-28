"""The ``hone-flow`` command line (extra ``cli``): run, resume, fork, inspect and review run folders.

``--flow module:attribute`` imports the workflow; read and review commands also accept ``--storage URL
--name NAME`` instead. Every command has ``--json``. Exit codes: 0 success (also a run stopped at a gate or
partial), 1 failure (a failed run, or a library error printed as ``error: ...``), 2 usage error.
"""

from __future__ import annotations

import importlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Annotated, Any, TypeVar, cast

try:
    import typer
    from typer.exceptions import TyperException
except ImportError:  # pragma: no cover - only without the extra
    raise SystemExit("the hone-flow command needs the 'cli' extra: pip install 'hone-flow[cli]'") from None

from pydantic import BaseModel

from hone_flow._records import strip_secrets
from hone_flow.errors import HoneFlowError
from hone_flow.history import RunHistory, open_runs
from hone_flow.run import Run
from hone_flow.types import Dir, File, Item
from hone_flow.workflow import Workflow

app = typer.Typer(help="Run AI workflows of plain-function steps as self-contained run folders.")

F = TypeVar("F", bound=Callable[..., Any])
Flow = Annotated[str | None, typer.Option("--flow", help="The workflow to import, as module:attribute.")]
FlowRequired = Annotated[str, typer.Option("--flow", help="The workflow to import, as module:attribute.")]
Storage = Annotated[str | None, typer.Option("--storage", help="Storage root (path or URL), with --name.")]
Name = Annotated[str | None, typer.Option("--name", help="Workflow name, with --storage.")]
Json = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
RunId = Annotated[str, typer.Argument(help="The run id.")]
StepOpt = Annotated[str, typer.Option("--step", help="The step (a gate for reviews).")]
ItemOpt = Annotated[str | None, typer.Option("--item", help="The item id.")]
Params = Annotated[list[str] | None, typer.Option("--param", help="A run param, name=value (JSON or text).")]
Automated = Annotated[
    bool, typer.Option("--automated", help="A program (a script, a model) decided, not a person.")
]
Label = Annotated[str | None, typer.Option("--label", help="A human name for the run.")]
Description = Annotated[str | None, typer.Option("--description", help="A longer description of the run.")]


def main() -> None:
    """Entry point of the ``hone-flow`` script."""
    app()


def reports_errors(command: F) -> F:
    """Library errors become ``error: ...`` on stderr and exit code 1 (never a traceback)."""

    @wraps(command)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return command(*args, **kwargs)
        except (typer.Exit, TyperException):  # usage errors (exit 2) and deliberate exits
            raise
        except HoneFlowError as exc:
            typer.echo(f"error: {strip_secrets(str(exc))}", err=True)
        except Exception as exc:  # e.g. storage credentials or network: still one line, no traceback
            typer.echo(f"error: {type(exc).__name__}: {strip_secrets(str(exc))}", err=True)
        raise typer.Exit(1)

    return cast(F, wrapper)


def load_flow(spec: str) -> Workflow:
    """Import ``module:attribute`` (the current directory is importable) and check it is a Workflow."""
    module_name, _, attribute = spec.partition(":")
    if not module_name or not attribute:
        raise typer.BadParameter(f"expected module:attribute, got {spec!r}")
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    try:
        flow = getattr(importlib.import_module(module_name), attribute)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise  # the user's module failed to import something: a real error, not a usage error
        raise typer.BadParameter(f"cannot load {spec!r}: {exc}") from None
    except AttributeError as exc:
        raise typer.BadParameter(f"cannot load {spec!r}: {exc}") from None
    if not isinstance(flow, Workflow):
        raise typer.BadParameter(f"{spec!r} is not a hone_flow.Workflow")
    return flow


def load_items(path: Path) -> list[Item]:
    """A JSON list of ``{"id": ..., "inputs": {...}}``.

    File and folder inputs are ``{"$file": path}`` / ``{"$dir": path}``, relative to the items file.
    """
    try:
        data: Any = json.loads(path.read_text())
        if not isinstance(data, list):
            raise TypeError("expected a JSON list of items")
        return [_item(cast(dict[str, Any], entry), path.parent) for entry in cast(list[Any], data)]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise typer.BadParameter(
            f"{path}: {exc!r} (expected [{{'id': ..., 'inputs': {{...}}}}, ...])"
        ) from None


def _item(entry: dict[str, Any], base: Path) -> Item:
    inputs: dict[str, Any] = {}
    raw: dict[str, Any] = entry.get("inputs") or {}
    for name, value in raw.items():
        ref = cast(dict[str, str], value) if isinstance(value, dict) else {}
        if "$file" in ref:
            inputs[name] = File(base / ref["$file"])
        elif "$dir" in ref:
            inputs[name] = Dir(base / ref["$dir"])
        else:
            inputs[name] = value
    return Item(str(entry["id"]), inputs)


def parse_params(values: list[str] | None) -> dict[str, Any]:
    """``name=value`` pairs; values are JSON when they parse as JSON, strings otherwise."""
    params: dict[str, Any] = {}
    for pair in values or []:
        name, sep, raw = pair.partition("=")
        if not sep:
            raise typer.BadParameter(f"--param expects name=value, got {pair!r}")
        try:
            params[name] = json.loads(raw)
        except json.JSONDecodeError:
            params[name] = raw
    return params


def parse_refresh(values: list[str] | None) -> dict[str, list[str] | None]:
    """``--refresh STEP`` (every item) or ``--refresh STEP=01,03`` (those items), repeatable."""
    found: dict[str, list[str] | None] = {}
    for value in values or []:
        step, sep, ids = value.partition("=")
        found[step] = split_ids(ids) or [] if sep else None
    return found


def parse_age(text: str) -> timedelta:
    """``30d``, ``12h`` or ``45m``."""
    units = {"d": "days", "h": "hours", "m": "minutes"}
    if len(text) < 2 or text[-1] not in units or not text[:-1].isdigit():
        raise typer.BadParameter(f"expected an age such as 30d, 12h or 45m, got {text!r}")
    return timedelta(**{units[text[-1]]: int(text[:-1])})


def split_ids(text: str | None) -> list[str] | None:
    return [part.strip() for part in text.split(",") if part.strip()] if text else None


def history(flow: str | None, storage: str | None, name: str | None) -> RunHistory:
    """The runs to work on: from ``--flow`` (attached) or ``--storage`` + ``--name`` (detached)."""
    if flow:
        wf = load_flow(flow)
        return RunHistory(wf.storage, wf.name, wf)
    if storage and name:
        return open_runs(storage, name)
    raise typer.BadParameter("pass --flow module:attribute, or --storage URL and --name NAME")


def emit(data: Any, as_json: bool, text: str) -> None:
    """Print ``data`` as JSON or ``text`` for people; secrets never reach the terminal."""
    typer.echo(json.dumps(strip_secrets(data), indent=2, sort_keys=True) if as_json else strip_secrets(text))


def run_result(run: Run, as_json: bool) -> None:
    """Print a run's outcome; a failed run exits with 1."""
    manifest = run.manifest
    counts: dict[str, int] = dict(Counter(cast(dict[str, str], manifest["state"]).values()))
    data = {
        "run_id": run.run_id,
        "workflow": run.workflow,
        "status": manifest["status"],
        "location": run.location,
        "states": counts,
        "pinned": manifest["pinned"],
    }
    states = ", ".join(f"{state} {n}" for state, n in sorted(counts.items()))
    emit(data, as_json, f"run {run.run_id} {manifest['status']}\n{run.location}\nsteps: {states}")
    if manifest["status"] == "failed":
        raise typer.Exit(1)


@app.command()
@reports_errors
def run(
    flow: FlowRequired,
    items: Annotated[Path, typer.Option("--items", help="JSON file with the items.")],
    param: Params = None,
    until: Annotated[str | None, typer.Option(help="Run only this step and what it needs.")] = None,
    items_filter: Annotated[str | None, typer.Option(help="Comma-separated item ids to run.")] = None,
    seed: Annotated[int, typer.Option(help="The run seed.")] = 0,
    label: Label = None,
    description: Description = None,
    as_json: Json = False,
) -> None:
    """Start a new run."""
    wf = load_flow(flow)
    started = wf.run(
        load_items(items),
        params=parse_params(param),
        until=until,
        items_filter=split_ids(items_filter),
        seed=seed,
        label=label,
        description=description,
    )
    run_result(started, as_json)


@app.command()
@reports_errors
def resume(
    run_id: RunId,
    flow: FlowRequired,
    until: Annotated[str | None, typer.Option(help="Continue only up to this step.")] = None,
    items: Annotated[str | None, typer.Option(help="Comma-separated item ids to continue.")] = None,
    as_json: Json = False,
) -> None:
    """Continue a run (same run id): pending, failed, interrupted, blocked and skipped work."""
    run_result(load_flow(flow).open_run(run_id).resume(until=until, items=split_ids(items)), as_json)


@app.command()
@reports_errors
def fork(
    run_id: RunId,
    flow: FlowRequired,
    refresh: Annotated[
        list[str] | None,
        typer.Option(help="A step to rerun with its downstream; STEP=01,03 reruns it for those items only."),
    ] = None,
    items: Annotated[str | None, typer.Option(help="Comma-separated item ids to keep.")] = None,
    param: Params = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the plan; write nothing.")] = False,
    label: Label = None,
    description: Description = None,
    as_json: Json = False,
) -> None:
    """Make a new run from this one: copy what did not change, rerun the rest."""
    result = (
        load_flow(flow)
        .open_run(run_id)
        .fork(
            parse_refresh(refresh),
            items=split_ids(items),
            params=parse_params(param) or None,
            dry_run=dry_run,
            label=label,
            description=description,
        )
    )
    if isinstance(result, Run):
        run_result(result, as_json)
        return
    rows = [asdict(row) for row in result.rows]
    lines = [
        f"{r['action']:5}  {r['step']}{'/' + r['item'] if r['item'] else ''}  {r['reason']}" for r in rows
    ]
    emit({"source_run_id": result.source_run_id, "rows": rows}, as_json, "\n".join(lines))


@app.command()
@reports_errors
def status(
    run_id: RunId, flow: Flow = None, storage: Storage = None, name: Name = None, as_json: Json = False
) -> None:
    """A run's status, location and step counts."""
    run_result(history(flow, storage, name).open_run(run_id), as_json)


@app.command()
@reports_errors
def show(
    run_id: RunId,
    step: StepOpt,
    item: ItemOpt = None,
    flow: Flow = None,
    storage: Storage = None,
    name: Name = None,
    as_json: Json = False,
) -> None:
    """A step's records: status, attempts, reviews, inputs and outputs."""
    records = [asdict(r) for r in history(flow, storage, name).open_run(run_id).steps(step, item)]
    if not records:
        raise HoneFlowError(f"run {run_id} has no step {step!r} for item {item!r}")
    emit(records, True, "")  # step records are JSON for people too


@app.command()
@reports_errors
def runs(
    flow: Flow = None,
    storage: Storage = None,
    name: Name = None,
    updated_since: Annotated[
        str | None, typer.Option(help="Only runs updated at or after this ISO time.")
    ] = None,
    as_json: Json = False,
) -> None:
    """List runs, newest first."""
    if updated_since is not None:
        try:
            datetime.fromisoformat(updated_since)
        except ValueError:
            raise typer.BadParameter(  # noqa: B904 - a usage error; the ValueError adds nothing
                f"expected an ISO-8601 time such as 2026-09-01T00:00:00Z, got {updated_since!r}"
            )
    found = history(flow, storage, name).runs(updated_since=updated_since)
    lines = [
        f"{s.run_id}  {s.status:16} {'pinned' if s.pinned else '':6} {s.label or '-'}  {s.location}"
        for s in found
    ]
    emit([asdict(s) for s in found], as_json, "\n".join(lines) or "no runs")


@app.command("label")
@reports_errors
def label_run(
    run_id: RunId,
    text: Annotated[str, typer.Argument(help="The run's new label (empty to remove it).")],
    description: Description = None,
    flow: Flow = None,
    storage: Storage = None,
    name: Name = None,
    as_json: Json = False,
) -> None:
    """Name a run for people (shown by runs, in notifications and in browsers)."""
    target = history(flow, storage, name).open_run(run_id)
    target.set_label(text, description)
    manifest = target.manifest
    data = {"run_id": run_id, "label": manifest["label"], "description": manifest["description"]}
    emit(data, as_json, f"{run_id}: {manifest['label'] or '(no label)'}")


@app.command()
@reports_errors
def approve(
    run_id: RunId,
    step: StepOpt,
    item: ItemOpt = None,
    note: str = "",
    automated: Automated = False,
    flow: Flow = None,
    storage: Storage = None,
    name: Name = None,
    as_json: Json = False,
) -> None:
    """Approve a gate's output; resume continues downstream."""
    history(flow, storage, name).open_run(run_id).approve(step, item, note=note, automated=automated)
    decided(run_id, step, item, "approved", as_json)


@app.command()
@reports_errors
def reject(
    run_id: RunId,
    step: StepOpt,
    note: Annotated[str, typer.Option(help="What to change.")],
    item: ItemOpt = None,
    automated: Automated = False,
    flow: Flow = None,
    storage: Storage = None,
    name: Name = None,
    as_json: Json = False,
) -> None:
    """Reject a gate's output; resume reruns its producers with the note as review_note."""
    history(flow, storage, name).open_run(run_id).reject(step, item, note=note, automated=automated)
    decided(run_id, step, item, "rejected", as_json)


@app.command()
@reports_errors
def edit(
    run_id: RunId,
    step: StepOpt,
    item: ItemOpt = None,
    flow: Flow = None,
    storage: Storage = None,
    name: Name = None,
    as_json: Json = False,
) -> None:
    """Edit a gate's JSON output in $EDITOR; resume continues downstream with the edited value."""
    target = history(flow, storage, name).open_run(run_id)
    current = target.output(step, item)
    value = current.model_dump(mode="json") if isinstance(current, BaseModel) else current
    edited = edit_json(value)
    target.edit(step, item, value=edited)
    decided(run_id, step, item, "edited", as_json)


def decided(run_id: str, step: str, item: str | None, decision: str, as_json: bool) -> None:
    emit({"run_id": run_id, "step": step, "item": item, "decision": decision}, as_json, decision)


def edit_json(value: Any) -> Any:
    """Open ``value`` as JSON in ``$EDITOR`` and return what was saved."""
    editor = os.environ.get("EDITOR")
    if not editor:
        raise HoneFlowError("set $EDITOR to edit a gate's output")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "output.json"
        path.write_text(json.dumps(value, indent=2, sort_keys=True))
        if subprocess.run([*shlex.split(editor), str(path)], check=False).returncode != 0:
            raise HoneFlowError(f"the editor {editor!r} failed; nothing was changed")
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise HoneFlowError(f"the edited output is not valid JSON: {exc}") from None


@app.command()
@reports_errors
def pin(
    run_id: RunId, flow: Flow = None, storage: Storage = None, name: Name = None, as_json: Json = False
) -> None:
    """Copy a finished run to pinned_runs/ (cleanup never deletes it)."""
    target = history(flow, storage, name).open_run(run_id)
    target.pin()
    emit({"run_id": run_id, "pinned": True}, as_json, f"pinned {run_id}")


@app.command()
@reports_errors
def cleanup(
    flow: Flow = None,
    storage: Storage = None,
    name: Name = None,
    keep_last: Annotated[int | None, typer.Option(help="Keep the newest N runs.")] = None,
    older_than: Annotated[
        str | None, typer.Option(help="Delete only runs older than this, e.g. 30d.")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report only; delete nothing.")] = False,
    as_json: Json = False,
) -> None:
    """Delete old run folders under runs/ (never pinned copies or runs in use)."""
    age = parse_age(older_than) if older_than else None
    report = history(flow, storage, name).cleanup(keep_last=keep_last, older_than=age, dry_run=dry_run)
    verb = "would delete" if dry_run else "deleted"
    text = (
        f"{verb}: {', '.join(report.deleted) or '-'}\nkept: {len(report.kept)}  locked: {len(report.locked)}"
    )
    emit(asdict(report), as_json, text)


@app.command("notify-retry")
@reports_errors
def notify_retry(
    run_id: RunId, flow: Flow = None, storage: Storage = None, name: Name = None, as_json: Json = False
) -> None:
    """Retry the pending and failed notification deliveries of a run."""
    results = history(flow, storage, name).open_run(run_id).retry_notifications()
    lines = [
        f"{d.destination}: {d.state} ({d.event}, attempt {d.attempts}){' ' + (d.error or '')}"
        for d in results
    ]
    emit([asdict(d) for d in results], as_json, "\n".join(lines) or "nothing to retry")


if __name__ == "__main__":  # python -m hone_flow.cli
    main()
