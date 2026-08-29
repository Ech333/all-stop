"""Webhook fan-out for kill-switch events.

Pure stdlib (urllib.request) - no requests dependency, matching this portfolio's zero-pip-deps
convention. The customer supplies their own webhook URL(s); nothing here ever talks to any
Northstar-operated endpoint.

A broadcast failure (webhook down, wrong URL, network blip) must never raise out of trip()/
reset() - by the time a broadcast fires, the kill switch state is ALREADY written. A dead webhook
is real information (worth surfacing), but it must not look like the trip itself failed, and it
must not block the trip from completing. send_broadcast() therefore always returns a bool rather
than raising, and callers that care about delivery should check the return value themselves.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

# URL substrings for services whose basic incoming-webhook contract accepts a simple
# {"text": "..."} payload. This is a heuristic, not a real content-type negotiation - a webhook
# proxy or a custom relay in front of one of these services would not match and falls through to
# the generic JSON shape below, which is the safer default for anything unrecognized.
_CHAT_WEBHOOK_MARKERS = ("hooks.slack.com", "webhook.office.com", "discord.com/api/webhooks")


@dataclass(frozen=True)
class BroadcastEvent:
    kind: str  # "tripped", "paused", or "reset"
    reason: str | None
    actor: str
    at: str


def _message_for(event: BroadcastEvent) -> str:
    if event.kind == "tripped":
        return f"ALL-STOP TRIPPED by {event.actor} at {event.at}: {event.reason}"
    if event.kind == "paused":
        return f"All-Stop PAUSED by {event.actor} at {event.at}: {event.reason}"
    return f"All-Stop reset by {event.actor} at {event.at}" + (
        f" (was: {event.reason})" if event.reason else ""
    )


def _payload_for(url: str, event: BroadcastEvent) -> bytes:
    if any(marker in url for marker in _CHAT_WEBHOOK_MARKERS):
        body = {"text": _message_for(event)}
    else:
        body = {
            "event": f"kill_switch_{event.kind}",
            "reason": event.reason,
            "actor": event.actor,
            "at": event.at,
        }
    return json.dumps(body).encode("utf-8")


def send_broadcast(url: str, event: BroadcastEvent, *, timeout: float = 5.0) -> bool:
    """POSTs `event` to `url`. Returns True on a 2xx response, False on any failure - never
    raises. `timeout` is deliberately short: a trip() call must not hang the caller because a
    webhook endpoint is slow or unreachable."""
    data = _payload_for(url, event)
    try:
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
