"""Notifications: tell Slack, Mattermost, Discord or any HTTP endpoint how a run ended.

What: ``Workflow(notifications=[...])`` sends ``run.completed``, ``run.failed`` and
``run.awaiting_review`` events to the destinations subscribed to them. Here an ``HttpWebhook`` talks to
``fk.testing.WebhookServer``, a local server that records requests and can simulate an outage.

How: create a destination with a ``name``, the environment variable holding its URL (``url_env``) and
its ``events``; set the variable; run. Each event is committed to ``manifest.json`` as ``pending`` before
it is sent; the result per destination (``delivered`` / ``failed``, attempts, error) is recorded after.
``run.retry_notifications()`` sends pending and failed deliveries again (CLI: ``hone-flow notify-retry``).

Why: an overnight run nobody noticed is wasted; but a Slack outage must never fail a finished run, and
webhook URLs are secrets: they are read at delivery time only and never written anywhere.
"""

import os
import tempfile
from pathlib import Path

import hone_flow as fk
from hone_flow.notifications import HttpWebhook, SlackWebhook
from hone_flow.testing import WebhookServer

with WebhookServer(status=503) as server:  # the endpoint is down at first
    os.environ["OPS_WEBHOOK_URL"] = server.url
    os.environ["SLACK_WEBHOOK_URL"] = server.url
    wf = fk.Workflow(
        "notify_demo",
        storage=tempfile.mkdtemp(),
        notifications=[
            HttpWebhook(
                name="ops",
                url_env="OPS_WEBHOOK_URL",
                events=("run.completed", "run.failed"),
                browse_url="https://studio.example/runs/{run_id}",
            ),
            SlackWebhook(name="team", url_env="SLACK_WEBHOOK_URL", events=("run.failed",)),
        ],
    )

    @wf.step()
    def encode(clip: str) -> str:
        return clip + ".mp4"

    run = wf.run([fk.Item("01", {"clip": "intro"}), fk.Item("02", {"clip": "outro"})])
    (event,) = run.manifest["notifications"]
    print(run.status, "|", event["event"], "|", event["summary"], "|", event["deliveries"]["ops"]["state"])
    assert run.status == "completed"  # the outage did not change the run
    assert event["deliveries"]["ops"]["state"] == "failed"
    assert "team" not in event["deliveries"]  # only destinations subscribed to run.completed

    server.status = 200  # the endpoint is back
    (delivery,) = run.retry_notifications()
    print(delivery)
    body = server.requests[-1].json()
    print("received:", body["event"], body["summary"], body["browse_url"])
    assert (delivery.state, delivery.attempts) == ("delivered", 2)
    assert body["event_id"] == f"{run.run_id}/run.completed/1"  # stable: receivers can deduplicate
    assert server.requests[-1].headers["Idempotency-Key"] == body["event_id"]
    manifest_text = Path(run.location, "manifest.json").read_text()
    assert server.url not in manifest_text  # the webhook URL is never recorded
