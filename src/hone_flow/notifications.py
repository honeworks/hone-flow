"""Run notifications (design §4.9): tell Slack, Mattermost, Discord or any HTTP endpoint when a run
completes, fails or waits for a review.

The event and its ``pending`` deliveries are committed to the manifest before anything is sent; each
delivery's result (``delivered`` / ``failed``) is recorded after. A failed delivery never changes the run
and never raises; ``run.retry_notifications()`` sends pending and failed deliveries again. Delivery is at
least once: the event id is in every payload (and the ``Idempotency-Key`` header of ``HttpWebhook``).
Webhook URLs are read from the environment at delivery time only and are never recorded.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from hone_flow._records import SECRET_ENV_NAMES, strip_secrets
from hone_flow._tracing import iso_now
from hone_flow.run_format import DeliveryInfo, Manifest, NotificationInfo, RunFolder, split_state_key
from hone_flow.types import Delivery

EVENTS = ("run.completed", "run.failed", "run.awaiting_review")
TIMEOUT_S = 10.0


@dataclass(frozen=True)
class Webhook:
    """A notification destination. ``url_env`` names the environment variable holding the webhook URL;
    ``browse_url`` is an optional link template with ``{run_id}`` and ``{workflow}``."""

    name: str
    url_env: str
    events: tuple[str, ...] = ("run.completed", "run.failed")
    browse_url: str | None = None
    kind: ClassVar[str] = ""  # the payload format; set by each destination type below

    def __post_init__(self) -> None:
        if not self.kind:
            raise TypeError("use SlackWebhook, MattermostWebhook, DiscordWebhook or HttpWebhook")
        unknown = [e for e in self.events if e not in EVENTS]
        if unknown:
            raise ValueError(f"unknown notification events {unknown}; choose from {list(EVENTS)}")
        try:
            (self.browse_url or "").format(run_id="r", workflow="w")
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"browse_url may only use {{run_id}} and {{workflow}}: {exc!r}") from None
        SECRET_ENV_NAMES.add(self.url_env)  # its value is stripped from everything hone-flow records


@dataclass(frozen=True)
class SlackWebhook(Webhook):
    """A Slack incoming webhook."""

    kind: ClassVar[str] = "slack"


@dataclass(frozen=True)
class MattermostWebhook(Webhook):
    """A Mattermost incoming webhook (Slack-compatible)."""

    kind: ClassVar[str] = "mattermost"


@dataclass(frozen=True)
class DiscordWebhook(Webhook):
    """A Discord webhook."""

    kind: ClassVar[str] = "discord"


@dataclass(frozen=True)
class HttpWebhook(Webhook):
    """Any HTTP endpoint: a JSON POST with the event's fields and an ``Idempotency-Key`` header."""

    kind: ClassVar[str] = "http"


def message_text(message: dict[str, Any]) -> str:
    lines = [
        f"{message['workflow']} · run {message['run_id']} {message['event'].removeprefix('run.')}",
        *([message["label"]] if message.get("label") else []),
        message["summary"],
        message["location"],
    ]
    if message["browse_url"]:
        lines.append(message["browse_url"])
    return "\n".join([*lines, f"event {message['event_id']}"])


def slack_payload(message: dict[str, Any]) -> dict[str, Any]:
    return {"text": message_text(message)}


def discord_payload(message: dict[str, Any]) -> dict[str, Any]:
    return {"content": message_text(message)}


def http_payload(message: dict[str, Any]) -> dict[str, Any]:
    return dict(message)


FORMATTERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "slack": slack_payload,
    "mattermost": slack_payload,
    "discord": discord_payload,
    "http": http_payload,
}


def add_event(
    folder: RunFolder, manifest: Manifest, destinations: Sequence[Webhook]
) -> NotificationInfo | None:
    """Append the event of the run's final status, with a ``pending`` delivery per subscribed destination.

    The caller commits the manifest before delivering (``deliver_event``).
    """
    event = f"run.{manifest.status}"
    targets = [d for d in destinations if event in d.events]
    if not targets:
        return None
    seq = 1 + sum(1 for n in manifest.notifications if n.event == event)
    info = NotificationInfo(
        id=f"{manifest.run_id}/{event}/{seq}",
        event=event,
        created_at=iso_now(),
        summary=str(strip_secrets(_safe_summary(folder, manifest))),
        deliveries={
            d.name: DeliveryInfo(state="pending", kind=d.kind, url_env=d.url_env, browse_url=d.browse_url)
            for d in targets
        },
    )
    manifest.notifications.append(info)
    return info


def _safe_summary(folder: RunFolder, manifest: Manifest) -> str:
    try:
        return summary(folder, manifest)
    except Exception:  # an unreadable step must not stop the status from being recorded
        return f"run {manifest.status}"


def summary(folder: RunFolder, manifest: Manifest) -> str:
    """``2 items completed · 0 failed``; the first error line when failed; the waiting gates."""
    items = [i.id for i in manifest.items]
    failed = sorted(
        {
            item
            for key, state in manifest.state.items()
            if state == "failed"
            for item in [split_state_key(key)[1] or "(global)"]
        }
    )
    if manifest.status == "awaiting_review":
        waiting = [split_state_key(k) for k, s in manifest.state.items() if s == "awaiting_review"]
        gates = sorted({step for step, _ in waiting})
        return f"{_items(len({item for _, item in waiting}))} awaiting review at {', '.join(gates)}"
    if manifest.status == "failed":
        key = next(k for k, s in manifest.state.items() if s == "failed")
        meta = folder.read_meta(*split_state_key(key))
        error = meta.error.message if meta and meta.error else "a step failed"
        return f"{len(failed)} of {_items(len(items))} failed · {key}: {error}"
    return f"{_items(len(items) - len(failed))} completed · {len(failed)} failed"


def _items(count: int) -> str:
    return f"{count} item" if count == 1 else f"{count} items"


def deliver_event(
    folder: RunFolder, manifest: Manifest, event: NotificationInfo, *, retry: bool = False
) -> list[Delivery]:
    """Send the event to its pending (and, with ``retry``, failed) destinations; record each result."""
    results: list[Delivery] = []
    for name, delivery in event.deliveries.items():
        if delivery.state == "pending" or (retry and delivery.state == "failed"):
            error = deliver(manifest, event, delivery)
            delivery.state = "failed" if error else "delivered"
            delivery.attempts += 1
            delivery.error, delivery.at = error, iso_now()
            folder.write_manifest(manifest)
            results.append(Delivery(event.id, event.event, name, delivery.state, delivery.attempts, error))
    return results


def deliver(manifest: Manifest, event: NotificationInfo, delivery: DeliveryInfo) -> str | None:
    """POST one delivery; return the error (never raising, never mentioning the URL) or ``None``."""
    SECRET_ENV_NAMES.add(delivery.url_env)  # also for runs opened without their workflow (detached retry)
    url = os.environ.get(delivery.url_env)
    if not url:
        return f"environment variable {delivery.url_env} is not set"
    if not url.startswith(("http://", "https://")):
        return f"environment variable {delivery.url_env} does not hold an http(s) URL"
    payload = FORMATTERS[delivery.kind](build_message(manifest, event, delivery))
    headers = {"Content-Type": "application/json"}
    if delivery.kind == "http":
        headers["Idempotency-Key"] = event.id
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310 - http(s) only
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 - the user's webhook
            response.read()
    except Exception as exc:  # delivery failures are recorded, never raised
        return str(
            strip_secrets(f"{type(exc).__name__}: {exc}".replace(url, f"<url from ${delivery.url_env}>"))
        )
    return None


def build_message(manifest: Manifest, event: NotificationInfo, delivery: DeliveryInfo) -> dict[str, Any]:
    """What every destination is told: never outputs, inputs or params (``label`` only when set)."""
    browse = delivery.browse_url
    label = {"label": manifest.label} if manifest.label else {}
    return label | {
        "event_id": event.id,
        "event": event.event,
        "workflow": manifest.workflow,
        "run_id": manifest.run_id,
        "summary": event.summary,
        "location": manifest.location,
        "browse_url": browse.format(run_id=manifest.run_id, workflow=manifest.workflow) if browse else None,
    }


def retry(folder: RunFolder) -> list[Delivery]:
    """Send every pending and failed delivery of a run once more."""
    manifest = folder.read_manifest()
    return [d for event in manifest.notifications for d in deliver_event(folder, manifest, event, retry=True)]
