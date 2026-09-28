"""AC-24: the CLI end to end (outputs, --json, exit codes, --storage/--name without --flow)."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hone_flow.cli import app

pytestmark = pytest.mark.e2e

FLOW = """
import hone_flow as fk

wf = fk.Workflow("cli_flow", storage="flows")

@wf.step()
def draft(text: str, style: fk.Param[str], ctx: fk.Context) -> dict:
    if text == "boom":
        raise ValueError("cannot draft sk-clisecret123456789")
    return {"text": f"{text} ({style})", "seed": ctx.seed}

@wf.gate()
def review(draft: dict) -> dict:
    return draft

@wf.step()
def publish(review: dict) -> str:
    return review["text"].upper()
"""

EDITOR = """
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data["text"] = "edited by hand"
json.dump(data, open(path, "w"))
"""


def invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args))
    return result.exit_code, result.output


def as_json(*args: str) -> Any:
    code, out = invoke(*args, "--json")
    assert code == 0, out
    return json.loads(out)


def setup(tmp_path: Path, module: str) -> None:
    (tmp_path / f"{module}.py").write_text(FLOW)
    (tmp_path / "items.json").write_text(
        json.dumps([{"id": "01", "inputs": {"text": "a"}}, {"id": "02", "inputs": {"text": "b"}}])
    )


def test_ac24_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    setup(tmp_path, "cliflow_main")
    flow = "cliflow_main:wf"
    started = as_json("run", "--flow", flow, "--items", "items.json", "--param", "style=noir")
    assert set(started) == {"run_id", "workflow", "status", "location", "states", "pinned"}
    assert started["status"] == "awaiting_review"
    run_id = started["run_id"]
    code, out = invoke("status", run_id, "--flow", flow)
    assert code == 0
    assert out.startswith(f"run {run_id} awaiting_review\n")
    detached = ["--storage", "flows", "--name", "cli_flow"]
    assert as_json("status", run_id, *detached)["states"] == {"awaiting_review": 2, "done": 2, "pending": 2}

    assert as_json("approve", run_id, "--step", "review", "--item", "01", *detached)["decision"] == "approved"
    editor = tmp_path / "editor.py"
    editor.write_text(EDITOR)
    monkeypatch.setenv("EDITOR", f"{sys.executable} {editor}")
    assert as_json("edit", run_id, "--step", "review", "--item", "02", *detached)["decision"] == "edited"
    resumed = as_json("resume", run_id, "--flow", flow)
    assert resumed["status"] == "completed"
    (record,) = as_json("show", run_id, "--step", "publish", "--item", "02", *detached)
    assert record["status"] == "done"
    code, out = invoke("show", run_id, "--step", "review", "--item", "02", *detached)
    assert json.loads(out)[0]["labels"] == ["edited"]

    plan = as_json("fork", run_id, "--flow", flow, "--dry-run", "--param", "style=bright")
    assert plan["source_run_id"] == run_id
    assert {r["reason"] for r in plan["rows"] if r["step"] == "draft"} == {"param_changed:style"}
    code, out = invoke("fork", run_id, "--flow", flow, "--dry-run", "--refresh", "publish")
    assert "run    publish/01  refresh_requested" in out
    forked = as_json("fork", run_id, "--flow", flow, "--refresh", "draft", "--items", "01")
    assert forked["status"] == "awaiting_review"

    listed = as_json("runs", *detached)
    assert [r["run_id"] for r in listed] == [forked["run_id"], run_id]
    assert set(listed[0]) == {
        "run_id",
        "workflow",
        "workflow_version",
        "status",
        "created_at",
        "updated_at",
        "fork_of",
        "pinned",
        "items",
        "location",
        "label",
        "description",
        "attempts",
    }
    assert as_json("runs", *detached, "--updated-since", "2999-01-01T00:00:00Z") == []
    assert as_json("pin", run_id, *detached) == {"run_id": run_id, "pinned": True}
    report = as_json("cleanup", *detached, "--keep-last", "1", "--dry-run")
    assert report == {"deleted": [run_id], "kept": [forked["run_id"]], "locked": []}
    code, out = invoke("cleanup", *detached, "--older-than", "30d")
    assert code == 0
    assert "deleted: -" in out
    assert as_json("notify-retry", run_id, *detached) == []
    code, out = invoke(
        "reject", forked["run_id"], "--step", "review", "--item", "01", "--note", "shorter", *detached
    )
    assert (code, out.strip()) == (0, "rejected")


def test_ac24_cli_exit_codes(tmp_path: Path) -> None:
    setup(tmp_path, "cliflow_codes")
    (tmp_path / "bad_items.json").write_text(json.dumps([{"id": "01", "inputs": {"text": "boom"}}]))
    code, out = invoke("run", "--flow", "cliflow_codes:wf", "--items", "bad_items.json", "--param", "style=x")
    assert code == 1  # a failed run
    assert "failed" in out
    run_id = out.split()[1]
    code, out = invoke("show", run_id, "--step", "draft", "--item", "01", "--flow", "cliflow_codes:wf")
    assert "sk-clisecret" not in out  # secrets never reach the terminal
    assert "***" in out
    code, out = invoke("status", "20000101T000000Z-000000", "--flow", "cliflow_codes:wf")
    assert code == 1
    assert out.startswith("error: no run '20000101T000000Z-000000'")
    code, out = invoke("approve", run_id, "--step", "draft", "--item", "01", "--flow", "cliflow_codes:wf")
    assert (code, "not a gate" in out) == (1, True)
    assert invoke("status", run_id)[0] == 2  # neither --flow nor --storage/--name
    assert invoke("run", "--flow", "nomodule", "--items", "items.json")[0] == 2
    assert invoke("cleanup", "--storage", "flows", "--name", "cli_flow", "--older-than", "soon")[0] == 2
    assert invoke("bogus-command")[0] == 2
    code, out = invoke("cleanup", "--storage", "flows", "--name", "cliflow")
    assert (code, out.strip()) == (1, "error: cleanup needs keep_last=, older_than= or both")


def test_ac24_cli_help_lists_every_command() -> None:
    code, out = invoke("--help")
    assert code == 0
    for command in (
        "run",
        "resume",
        "fork",
        "status",
        "show",
        "runs",
        "approve",
        "edit",
        "reject",
        "pin",
        "cleanup",
        "notify-retry",
    ):
        assert command in out


NOTIFY_FLOW = """
import hone_flow as fk
from hone_flow.notifications import HttpWebhook

ops = HttpWebhook(name="ops", url_env="CLI_HOOK_URL")
wf = fk.Workflow("notify_flow", storage="flows", notifications=[ops])

@wf.step()
def one(text: str) -> str:
    return text
"""


def test_ac24_cli_notify_retry_and_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from hone_flow.testing import WebhookServer

    (tmp_path / "cliflow_notify.py").write_text(NOTIFY_FLOW)
    (tmp_path / "items.json").write_text(json.dumps([{"id": "01", "inputs": {"text": "a"}}]))
    with WebhookServer(status=500) as server:
        monkeypatch.setenv("CLI_HOOK_URL", f"{server.url}?token=PlantedCliToken12345")
        started = as_json("run", "--flow", "cliflow_notify:wf", "--items", "items.json")
        for command in (
            ["status", started["run_id"], "--json"],
            ["show", started["run_id"], "--step", "one"],
            ["notify-retry", started["run_id"]],
            ["runs", "--json"],
        ):
            code, out = invoke(*command, "--storage", "flows", "--name", "notify_flow")
            assert code == 0, out
            assert "PlantedCliToken12345" not in out  # the webhook URL never reaches the terminal
        server.status = 200
        (delivery,) = as_json(
            "notify-retry", started["run_id"], "--storage", "flows", "--name", "notify_flow"
        )
        assert delivery == {
            "event_id": f"{started['run_id']}/run.completed/1",
            "event": "run.completed",
            "destination": "ops",
            "state": "delivered",
            "attempts": 3,
            "error": None,
        }
    code, out = invoke("runs", "--storage", "flows", "--name", "notify_flow", "--updated-since", "yesterday")
    assert code == 2
    monkeypatch.setenv("EDITOR", "false")
    code, out = invoke(
        "edit", started["run_id"], "--step", "one", "--item", "01", "--flow", "cliflow_notify:wf"
    )
    assert (code, out.startswith("error: ")) == (1, True)
    (tmp_path / "broken_flow.py").write_text("import not_installed_dependency\n")
    code, out = invoke("status", "x", "--flow", "broken_flow:wf")
    assert code == 1  # the user's module is broken: not a usage error
    assert "not_installed_dependency" in out


def test_ac24_cli_partial_runs(tmp_path: Path) -> None:
    setup(tmp_path, "cliflow_partial")
    flow = "cliflow_partial:wf"
    started = as_json(
        "run",
        "--flow",
        flow,
        "--items",
        "items.json",
        "--param",
        "style=x",
        "--until",
        "draft",
        "--items-filter",
        "01",
    )
    assert started["status"] == "partial"
    assert started["states"] == {"done": 1, "skipped": 5}
    resumed = as_json("resume", started["run_id"], "--flow", flow, "--items", "02", "--until", "draft")
    assert resumed["states"] == {"done": 2, "skipped": 4}
