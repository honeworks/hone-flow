"""AC-17: run events reach the subscribed destinations, committed as pending before delivery."""

import json
from pathlib import Path

import pytest

import hone_flow as fk
from hone_flow.notifications import HttpWebhook, SlackWebhook
from hone_flow.testing import WebhookRequest, WebhookServer
from tests.e2e.song_video import song_video, write_songs

pytestmark = pytest.mark.e2e


def test_ac17_notifications(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen_during_request: list[dict[str, object]] = []
    storage = tmp_path / "flows"

    def check_manifest(request: WebhookRequest) -> None:  # the server reads the manifest mid-request
        (manifest_path,) = storage.glob("song_video/runs/*/manifest.json")
        manifest = json.loads(manifest_path.read_text())
        assert manifest["status"] in ("awaiting_review", "completed")  # the status was committed first
        seen_during_request.append(manifest["notifications"][-1]["deliveries"])

    with WebhookServer(on_request=check_manifest) as http, WebhookServer() as slack:
        monkeypatch.setenv("OPS_WEBHOOK_URL", http.url)
        monkeypatch.setenv("SLACK_URL", slack.url)
        destinations = [
            HttpWebhook(
                name="ops",
                url_env="OPS_WEBHOOK_URL",
                events=("run.completed", "run.awaiting_review"),
                browse_url="https://studio.example/{workflow}/{run_id}",
            ),
            SlackWebhook(name="studio", url_env="SLACK_URL", events=("run.completed",)),
        ]
        wf = song_video(storage, notifications=destinations)
        run = wf.run(write_songs(tmp_path), params={"style": "noir"})
        assert run.status == "awaiting_review"
        assert len(http.requests) == 1  # awaiting_review: only the destination subscribed to it
        assert slack.requests == []
        assert seen_during_request == [
            {
                "ops": {
                    "at": None,
                    "attempts": 0,
                    "browse_url": "https://studio.example/{workflow}/{run_id}",
                    "error": None,
                    "kind": "http",
                    "state": "pending",
                    "url_env": "OPS_WEBHOOK_URL",
                }
            }
        ]
        payload = http.requests[0].json()
        event_id = f"{run.run_id}/run.awaiting_review/1"
        assert payload == {
            "event_id": event_id,
            "event": "run.awaiting_review",
            "workflow": "song_video",
            "run_id": run.run_id,
            "summary": "2 items awaiting review at review_shotlist",
            "location": run.location,
            "browse_url": f"https://studio.example/song_video/{run.run_id}",
        }
        assert http.requests[0].headers["Idempotency-Key"] == event_id
        assert "noir" not in http.requests[0].body.decode()  # never params, inputs or outputs
        (event,) = run.manifest["notifications"]
        assert event["deliveries"]["ops"]["state"] == "delivered"
        assert event["deliveries"]["ops"]["attempts"] == 1

        for item in ("01", "02"):
            run.approve(step="review_shotlist", item=item)
        run.resume()
        assert run.status == "completed"
        assert [r.json()["event"] for r in http.requests] == ["run.awaiting_review", "run.completed"]
        (slack_request,) = slack.requests
        text = slack_request.json()["text"]
        assert text.startswith(f"song_video · run {run.run_id} completed\n2 items completed · 0 failed\n")
        assert f"event {run.run_id}/run.completed/1" in text
        assert [n["event"] for n in run.manifest["notifications"]] == ["run.awaiting_review", "run.completed"]
        assert {
            n["deliveries"][d]["state"] for n in run.manifest["notifications"] for d in n["deliveries"]
        } == {"delivered"}


def test_ac17_notifications_destination_rules(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown notification events"):
        HttpWebhook(name="x", url_env="X", events=("run.started",))
    with pytest.raises(fk.WorkflowDefinitionError, match="unique names"):
        fk.Workflow(
            "w",
            storage=tmp_path,
            notifications=[HttpWebhook(name="x", url_env="A"), SlackWebhook(name="x", url_env="B")],
        )


def test_ac17_notifications_bad_browse_url() -> None:
    with pytest.raises(ValueError, match="browse_url may only use"):
        HttpWebhook(name="x", url_env="X", browse_url="https://ui/{run}")
