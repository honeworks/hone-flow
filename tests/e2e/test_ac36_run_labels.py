"""AC-36: run labels (design change 0009): a human name and description per run."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import hone_flow as fk
from hone_flow.cli import app
from hone_flow.notifications import HttpWebhook
from hone_flow.testing import WebhookServer

pytestmark = pytest.mark.e2e


def songs(storage: Any, **options: Any) -> fk.Workflow:
    wf = fk.Workflow("songs", storage=storage, **options)

    @wf.global_step()
    def idea(topic: fk.Param[str], ctx: fk.Context) -> dict[str, str]:
        title = f"Songs about {topic}"
        ctx.set_run_label(title, description=f"an album about {topic}")
        return {"title": title}

    @wf.step()
    def lyrics(idea: dict[str, str], line: str, ctx: fk.Context) -> str:
        ctx.set_run_label(f"{idea['title']} (lyrics)")  # a failed attempt changes nothing
        if line == "boom":
            raise ValueError("no rhyme")
        return f"{idea['title']}: {line}"

    return wf


def test_ac36_labels_from_the_start_and_from_a_step(tmp_path: Path) -> None:
    wf = songs(tmp_path / "flows")
    run = wf.run(
        [fk.Item("01", {"line": "la"})], params={"topic": "rain"}, label="Rain album", description="first try"
    )
    manifest = run.manifest
    # each step renamed the run once its result was committed (the last one wins)
    assert manifest["label"] == "Songs about rain (lyrics)"
    assert manifest["description"] == "an album about rain"

    wf2 = songs(tmp_path / "flows2")
    failed = wf2.run([fk.Item("01", {"line": "boom"})], params={"topic": "sun"}, label="Sun")
    assert failed.status == "failed"
    assert (failed.manifest["label"], failed.manifest["description"]) == (
        "Songs about sun",
        "an album about sun",
    )

    (summary,) = fk.open_runs(tmp_path / "flows2", "songs").runs()
    assert (summary.label, summary.description) == ("Songs about sun", "an album about sun")
    run_span = next(s for s in failed.spans() if s["name"] == "hone.flow.run")
    assert run_span["attributes"]["hone.flow.run.label"] == "Songs about sun"


def test_ac36_set_label_fork_and_secrets(tmp_path: Path) -> None:
    wf = fk.Workflow("plain", storage=tmp_path / "flows")

    @wf.step()
    def double(x: int) -> int:
        return 2 * x

    run = wf.run([fk.Item("a", {"x": 1})])
    assert run.manifest["label"] is None  # old and unlabelled runs simply have none
    detached = fk.open_runs(tmp_path / "flows", "plain").open_run(run.run_id)
    detached.set_label("  Leads 2026-09-28  ", description="45 posts")
    assert (run.manifest["label"], run.manifest["description"]) == ("Leads 2026-09-28", "45 posts")
    detached.set_label("Leads (redo)")  # description=None keeps the description
    assert (run.manifest["label"], run.manifest["description"]) == ("Leads (redo)", "45 posts")

    forked = run.fork(refresh=("double",))
    assert (forked.manifest["label"], forked.manifest["description"]) == ("Leads (redo) (fork)", "45 posts")
    named = run.fork(label="Second opinion", description="")
    assert (named.manifest["label"], named.manifest["description"]) == ("Second opinion", None)

    run.set_label("key sk-labelsecret1234567890")
    assert run.manifest["label"] == "key ***"
    run.set_label(None)
    assert run.manifest["label"] is None
    run.pin()
    pinned = fk.open_runs(tmp_path / "flows", "plain")
    shutil.rmtree(tmp_path / "flows" / "plain" / "runs" / run.run_id)
    with pytest.raises(fk.HoneFlowError, match="pinned archive"):
        pinned.open_run(run.run_id).set_label("nope")


def test_ac36_label_while_running_is_locked(tmp_path: Path) -> None:
    wf = fk.Workflow("locked", storage=tmp_path / "flows")
    seen: list[str] = []

    @wf.step()
    def step(x: int, ctx: fk.Context) -> int:
        other = fk.open_runs(tmp_path / "flows", "locked").open_run(ctx.run_id)
        try:
            other.set_label("from outside")
        except fk.RunLocked as exc:
            seen.append(str(exc))
        return x

    wf.run([fk.Item("a", {"x": 1})])
    assert len(seen) == 1


def test_ac36_notifications_and_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with WebhookServer() as http:
        monkeypatch.setenv("OPS_URL", http.url)
        wf = fk.Workflow(
            "cli_labels",
            storage=tmp_path / "flows",
            notifications=[HttpWebhook(name="ops", url_env="OPS_URL")],
        )

        @wf.step()
        def double(x: int) -> int:
            return 2 * x

        run = wf.run([fk.Item("a", {"x": 1})], label="Weekly report")
        assert http.requests[0].json()["label"] == "Weekly report"

    runner = CliRunner()
    detached = ["--storage", str(tmp_path / "flows"), "--name", "cli_labels"]
    result = runner.invoke(
        app, ["label", run.run_id, "Weekly report (final)", "--description", "ok", *detached]
    )
    assert result.exit_code == 0, result.output
    listed = runner.invoke(app, ["runs", *detached])
    assert "Weekly report (final)" in listed.output
    data = json.loads(runner.invoke(app, ["runs", *detached, "--json"]).output)
    assert (data[0]["label"], data[0]["description"]) == ("Weekly report (final)", "ok")
