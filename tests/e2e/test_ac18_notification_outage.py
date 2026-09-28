"""AC-18: an outage never fails a run; retries deliver later; webhook URLs never leak."""

import logging
import socket
from collections.abc import Sequence
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.notifications import DiscordWebhook, HttpWebhook, MattermostWebhook, Webhook
from hone_flow.testing import WebhookServer

pytestmark = pytest.mark.e2e

TOKEN = "PlantedWebhookToken99887766"  # noqa: S105 - a fake secret, deliberately not sk-... shaped


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def flow(storage: Path, destinations: Sequence[Webhook]) -> fk.Workflow:
    wf = fk.Workflow("notified", storage=storage, notifications=destinations)

    @wf.step()
    def one(text: str) -> str:
        return text

    return wf


def test_ac18_notification_outage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    with WebhookServer(status=500) as down:
        monkeypatch.setenv("DOWN_URL", f"{down.url}?token={TOKEN}")
        monkeypatch.setenv("GONE_URL", f"http://127.0.0.1:{closed_port()}/hook?token={TOKEN}")
        destinations = [
            HttpWebhook(name="down", url_env="DOWN_URL"),
            HttpWebhook(name="gone", url_env="GONE_URL"),
            HttpWebhook(name="unset", url_env="NOT_SET_URL"),
        ]
        run = flow(tmp_path, destinations).run([fk.Item("01", {"text": "a"})])
        assert run.status == "completed"  # a delivery failure never changes the run
        (event,) = run.manifest["notifications"]
        down_state, gone_state, unset_state = (event["deliveries"][d] for d in ("down", "gone", "unset"))
        assert (down_state["state"], down_state["attempts"]) == ("failed", 1)
        assert "500" in down_state["error"]
        assert gone_state["state"] == "failed"
        assert "Connection refused" in gone_state["error"] or "refused" in gone_state["error"].lower()
        assert unset_state["error"] == "environment variable NOT_SET_URL is not set"

        down.status = 200  # the server recovers
        monkeypatch.setenv("NOT_SET_URL", down.url)
        results = run.retry_notifications()
        assert {(r.destination, r.state, r.attempts) for r in results} == {
            ("down", "delivered", 2),
            ("gone", "failed", 2),
            ("unset", "delivered", 2),
        }
        assert all(isinstance(r, fk.Delivery) for r in results)
        (event,) = run.manifest["notifications"]
        assert event["deliveries"]["down"]["state"] == "delivered"
        assert len(down.requests) == 3  # two failed attempts' first try, then the retries
        again = run.retry_notifications()  # only what is still pending or failed
        assert [(r.destination, r.state, r.attempts) for r in again] == [("gone", "failed", 3)]

    for path in Path(run.location).rglob("*"):
        if path.is_file():
            text = path.read_text()
            assert TOKEN not in text, path
    assert TOKEN not in caplog.text
    assert all(TOKEN not in str(span) for span in run.spans())


def test_ac18_notification_outage_other_formats(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with WebhookServer() as server:
        monkeypatch.setenv("MM_URL", server.url)
        monkeypatch.setenv("DC_URL", server.url)
        run = flow(
            tmp_path,
            [MattermostWebhook(name="mm", url_env="MM_URL"), DiscordWebhook(name="dc", url_env="DC_URL")],
        ).run([fk.Item("01", {"text": "a"})])
        bodies = [r.json() for r in server.requests]
        assert set(bodies[0]) == {"text"}  # Mattermost takes the Slack format
        assert set(bodies[1]) == {"content"}
        assert "Idempotency-Key" not in server.requests[0].headers
        assert run.status == "completed"


def test_ac18_notification_outage_secret_in_step_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOOK_URL", f"https://hooks.example/services/{TOKEN}")
    wf = fk.Workflow(
        "leaky",
        storage=tmp_path,
        notifications=[HttpWebhook(name="h", url_env="HOOK_URL", events=("run.completed",))],  # not sent
    )

    @wf.step()
    def boom(text: str) -> str:
        import os

        raise RuntimeError(f"posting to {os.environ['HOOK_URL']} failed")

    run = wf.run([fk.Item("01", {"text": "a"})])
    assert run.status == "failed"
    for path in Path(run.location).rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text(), path
    assert "***" in run.steps("boom", "01")[0].error["message"]  # type: ignore[index]


def test_ac18_notification_outage_failed_runs_are_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with WebhookServer() as server:
        monkeypatch.setenv("FAIL_URL", f"{server.url}?token={TOKEN}")
        wf = fk.Workflow(
            "failing", storage=tmp_path, notifications=[HttpWebhook(name="ops", url_env="FAIL_URL")]
        )

        @wf.step()
        def boom(text: str) -> str:
            import os

            raise RuntimeError(f"posting to {os.environ['FAIL_URL']} rejected")

        run = wf.run([fk.Item("01", {"text": "a"})])
        (request,) = server.requests
        body = request.json()
        assert body["event"] == "run.failed"
        assert (
            body["summary"] == "1 of 1 item failed · boom/01: RuntimeError: posting to *** rejected"
        )  # env value
        assert TOKEN not in request.body.decode()
        detached = fk.open_runs(tmp_path, "failing").open_run(run.run_id)
        assert detached.retry_notifications() == []  # nothing pending or failed


def test_ac18_notification_outage_url_rewrites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    url = f"https://hooks.example/{TOKEN}"

    def refuse(request: object, timeout: float) -> object:
        raise OSError(f"cannot reach {url}")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setenv("REWRITE_URL", url)
    monkeypatch.setenv("FILE_URL", "file:///etc/passwd")
    run = flow(
        tmp_path, [HttpWebhook(name="r", url_env="REWRITE_URL"), HttpWebhook(name="f", url_env="FILE_URL")]
    ).run([fk.Item("01", {"text": "a"})])
    deliveries = run.manifest["notifications"][0]["deliveries"]
    assert deliveries["r"]["error"] == "OSError: cannot reach <url from $REWRITE_URL>"
    assert deliveries["f"]["error"] == "environment variable FILE_URL does not hold an http(s) URL"
    with pytest.raises(TypeError, match="use SlackWebhook"):
        Webhook(name="x", url_env="X")
