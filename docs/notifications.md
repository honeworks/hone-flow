# Notifications

hone-flow can tell Slack, Mattermost, Discord or any HTTP endpoint when a run completes, fails or waits
for a review. Delivery uses the standard library (`urllib.request`, 10 s timeout); no extra is needed.

Example: [`notifications.py`](../examples/notifications.py).

## Destinations

Destinations live in `hone_flow.notifications` and are small frozen dataclasses with the same fields:

| Class | Payload |
|---|---|
| `SlackWebhook` | Slack incoming webhook: `{"text": ...}` |
| `MattermostWebhook` | Mattermost incoming webhook (Slack-compatible): `{"text": ...}` |
| `DiscordWebhook` | Discord webhook: `{"content": ...}` |
| `HttpWebhook` | any endpoint: a JSON object with the message fields below, and an `Idempotency-Key` header |

| Field | Default | Meaning |
|---|---|---|
| `name` | required | the destination's name in the manifest (unique within a workflow) |
| `url_env` | required | the environment variable that holds the webhook URL |
| `events` | `("run.completed", "run.failed")` | the events this destination receives |
| `browse_url` | `None` | a link template with `{run_id}` and `{workflow}`, e.g. `https://studio.example/runs/{run_id}` |

```python
import os
import tempfile
from contextlib import ExitStack

import hone_flow as fk
from hone_flow.notifications import HttpWebhook, SlackWebhook
from hone_flow.testing import WebhookServer

stack = ExitStack()  # in a test: `with WebhookServer(status=503) as server:`
server = stack.enter_context(WebhookServer(status=503))  # a local endpoint, down at first
os.environ["OPS_WEBHOOK_URL"] = server.url
os.environ["SLACK_WEBHOOK_URL"] = server.url

wf = fk.Workflow(
    "notify_demo",
    storage=tempfile.mkdtemp(),
    notifications=[
        HttpWebhook(
            name="ops",
            url_env="OPS_WEBHOOK_URL",
            events=("run.completed", "run.failed", "run.awaiting_review"),
            browse_url="https://studio.example/runs/{run_id}",
        ),
        SlackWebhook(name="team", url_env="SLACK_WEBHOOK_URL", events=("run.failed",)),
    ],
)


@wf.step()
def encode(clip: str) -> str:
    return clip + ".mp4"
```

## Events

| Event | Sent when a run, resume or fork call ends with status |
|---|---|
| `run.completed` | `completed` |
| `run.failed` | `failed` |
| `run.awaiting_review` | `awaiting_review` |

Each event goes to every destination subscribed to it. Calls that end `partial`, review decisions,
pinning and cleanup send nothing.

## Delivery states

At the end of a call hone-flow commits the run status **and** the event, with a `pending` delivery per
destination, to `manifest.json` **before** it sends anything. After each attempt the destination's entry
records `delivered` or `failed`, with the error, the attempt count and the time:

```json
{"id": "20260927T140311Z-3f9a1c/run.completed/1", "event": "run.completed", "created_at": "…",
 "summary": "2 items completed · 0 failed",
 "deliveries": {"ops": {"state": "failed", "attempts": 1, "error": "HTTPError: HTTP Error 503: …",
                        "at": "…", "kind": "http", "url_env": "OPS_WEBHOOK_URL",
                        "browse_url": "https://studio.example/runs/{run_id}"}}}
```

A delivery failure never changes the run status and never raises from `run`, `resume` or `fork`.

```python
run = wf.run([fk.Item("01", {"clip": "intro"}), fk.Item("02", {"clip": "outro"})])
(event,) = run.manifest["notifications"]
assert run.status == "completed"  # the outage did not change the run
assert event["summary"] == "2 items completed · 0 failed"
assert event["deliveries"]["ops"]["state"] == "failed"
assert "team" not in event["deliveries"]  # only destinations subscribed to run.completed
```

## Retrying

There is no background retry. `run.retry_notifications()` (CLI: `hone-flow notify-retry RUN_ID`) sends
every `pending` and `failed` delivery of the run once more and returns an `fk.Delivery(event_id, event,
destination, state, attempts, error)` per delivery it tried. It works on a run opened without the
workflow's code too; the URL is still read from the environment.

```python
server.status = 200  # the endpoint is back
(delivery,) = run.retry_notifications()
assert (delivery.destination, delivery.state, delivery.attempts) == ("ops", "delivered", 2)
assert run.retry_notifications() == []  # nothing left to send
```

## The message

Every destination is told the workflow, the run id, the event, a short summary, the run's location, the
formatted `browse_url` (when set), the event id and, when the run has one, its `label`
([run labels](read-api.md#run-labels)). Never outputs, inputs or params. The summary is
`2 items completed · 0 failed`, the first error for `run.failed`, or the waiting gates for
`run.awaiting_review`. `HttpWebhook` posts these fields as JSON:

```python
body = server.requests[-1].json()
assert sorted(body) == ["browse_url", "event", "event_id", "location", "run_id", "summary", "workflow"]
assert body["browse_url"] == f"https://studio.example/runs/{run.run_id}"
```

## At least once

Delivery is **at least once**: if the process crashes after the endpoint accepted a message but before
`delivered` was written, a retry sends it again. The event id, `<run_id>/<event>/<n>`, is stable and is
in every payload (and in the `Idempotency-Key` header of `HttpWebhook`), so a receiver can deduplicate.

```python
assert body["event_id"] == f"{run.run_id}/run.completed/1"
assert server.requests[-1].headers["Idempotency-Key"] == body["event_id"]
```

## Secrets

A webhook URL is a secret. It is read from `os.environ[url_env]` at delivery time only and never appears
in manifests, metadata, reports, spans, logs, CLI output or error messages (errors show
`<url from $OPS_WEBHOOK_URL>` instead). The manifest records only the variable's name. A missing
variable is a `failed` delivery with the error "environment variable OPS_WEBHOOK_URL is not set".

```python
from pathlib import Path

assert server.url not in Path(run.location, "manifest.json").read_text()
del os.environ["OPS_WEBHOOK_URL"]
failing = wf.run([fk.Item("03", {"clip": "credits"})])
error = failing.manifest["notifications"][0]["deliveries"]["ops"]["error"]
assert error == "environment variable OPS_WEBHOOK_URL is not set"
```

## Testing your setup

`hone_flow.testing.WebhookServer(status=200, on_request=None)` is a local HTTP server for tests. Use it as
a context manager; its `url` accepts POSTs, `requests` records each one (`path`, `headers`, `body`,
`json()`), and changing `status` scripts an outage. `on_request` is called with each request before the
answer is sent (for example to read the manifest and see the event still `pending`).

```python
stack.close()  # stops the server
```
