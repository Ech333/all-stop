"""KillSwitch - a file-backed, multi-process-safe kill switch.

The problem this closes: every guard in this portfolio (MCP Gateway's ToolCallLimiter, Leviathan
Platform's BudgetGuard) is in-memory, per-process, reset on restart. There is no way for a human
to say "stop every agent right now" across processes - only per-run limits an agent can trip on
its own. This is the missing piece: a human-triggered, cross-process stop that any guard can
check cheaply on every call.

Deliberately NOT a daemon, NOT a network service, NOT anything that phones home. State lives in
one JSON file the caller points at - a shared filesystem path (a local disk, a network share, a
synced folder) is the entire "multi-process" story. This matches every other product in this
portfolio's self-hosted-only, zero-telemetry promise: nothing here ever leaves the customer's own
infrastructure unless THEY configure a webhook URL that does.

Atomic writes (temp file + os.replace) so a crash mid-write can never leave a reader looking at a
half-written, corrupt state file - os.replace is atomic on both POSIX and Windows when source and
destination are on the same filesystem, which they always are here (same directory).

Three states, not two (added 2026-08-29, closing a gap this portfolio's own site had stated
plainly rather than hidden: "All-Stop is a binary trip/reset today, not a graduated pause" -
AIUC-1's C009 requirement asks for human-in-the-loop pause/redirect "without requiring full
technical shutdown"):

  - clear   - normal operation.
  - paused  - a distinct, auditable, human-triggered state for "hold for review" that is NOT a
              full stop. Exists so an integrator's own policy can choose to treat it differently
              from a trip (e.g. degrade rather than deny) instead of every softer signal getting
              collapsed into the same hard stop.
  - tripped - full stop, unchanged from before. Existing `tripped()` callers are unaffected: it
              returns True only for this state, never for "paused" - a duck-typed integration
              written against `tripped()` alone keeps its exact prior behavior, and does not need
              to change to remain correct. Adopting the softer state is opt-in via the new
              `paused()` method.

Honest about what pause does NOT do yet: this is the state primitive and its audit trail, not a
redirect/routing mechanism. No guard in this portfolio currently treats "paused" differently from
"tripped" in its own enforcement decision by default - the state exists, is real, and is
independently auditable, but building the "route paused calls to a human review queue instead of
denying them outright" policy layer on top is future work, not implied by this existing.

Pure standard library.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .broadcast import BroadcastEvent, send_broadcast

STATE_CLEAR = "clear"
STATE_PAUSED = "paused"
STATE_TRIPPED = "tripped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SwitchStatus:
    state: str
    tripped: bool
    paused: bool
    reason: str | None
    actor: str | None
    tripped_at: str | None
    reset_at: str | None
    paused_at: str | None
    resumed_at: str | None


_DEFAULT_STATUS = {
    "state": STATE_CLEAR,
    # "tripped" is kept as a literal boolean key in the JSON file too, alongside "state" - not
    # just for SwitchStatus's own field, but so a non-Python reader that predates this change (a
    # customer's own script grepping the raw file for "tripped": true, per this package's own
    # documented "one JSON file is the entire interop mechanism" design) keeps working unmodified.
    "tripped": False,
    "reason": None,
    "actor": None,
    "tripped_at": None,
    "reset_at": None,
    "paused_at": None,
    "resumed_at": None,
}


class KillSwitch:
    """One kill switch, backed by one JSON file at `path`.

    Every call to `tripped()`/`paused()`/`status()` reads the file fresh from disk - no caching,
    no polling interval to tune, no daemon to keep alive. The cost is one small file read per
    check, which is the right trade for "a human needs this to actually work under pressure" over
    "this is the fastest possible check."""

    def __init__(self, path: str | Path, webhook_urls: tuple[str, ...] = ()):
        self.path = Path(path)
        self.webhook_urls = tuple(webhook_urls)

    def _read(self) -> dict:
        if not self.path.exists():
            return dict(_DEFAULT_STATUS)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            # A corrupt or unreadable state file must fail toward the SAFE direction for a kill
            # switch specifically: unreadable state is treated as "don't know, so assume tripped"
            # - the opposite failure direction from almost everything else in this portfolio,
            # because for THIS primitive, a silent false "not tripped" is the dangerous outcome,
            # not a spurious stop. Fails all the way to TRIPPED, not just PAUSED - "don't know" is
            # not a case for the softer state.
            return {
                **_DEFAULT_STATUS, "state": STATE_TRIPPED, "tripped": True,
                "reason": "state file unreadable/corrupt - failing closed",
            }
        merged = dict(_DEFAULT_STATUS)
        merged.update(data)
        if "state" not in data:
            # Backward compat: a file written by a pre-pause version of this library has
            # "tripped": bool but no "state" key at all - derive it rather than defaulting to
            # "clear", or upgrading this library on an existing deployment would silently clear
            # every currently-tripped switch on first read.
            merged["state"] = STATE_TRIPPED if merged.get("tripped") else STATE_CLEAR
        return merged

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(self.path.parent), prefix=".allstop-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def tripped(self) -> bool:
        return self._read()["state"] == STATE_TRIPPED

    def paused(self) -> bool:
        return self._read()["state"] == STATE_PAUSED

    def status(self) -> SwitchStatus:
        d = self._read()
        state = d["state"]
        return SwitchStatus(
            state=state, tripped=state == STATE_TRIPPED, paused=state == STATE_PAUSED,
            reason=d["reason"], actor=d["actor"],
            tripped_at=d["tripped_at"], reset_at=d["reset_at"],
            paused_at=d["paused_at"], resumed_at=d["resumed_at"],
        )

    def trip(self, reason: str, actor: str, *, webhook_urls: tuple[str, ...] | None = None) -> SwitchStatus:
        if not reason.strip():
            raise ValueError("reason must not be empty - a kill switch trip with no reason is not auditable")
        if not actor.strip():
            raise ValueError("actor must not be empty - a kill switch trip with no actor is not auditable")
        prior = self._read()
        data = {
            **_DEFAULT_STATUS, "state": STATE_TRIPPED, "tripped": True,
            "reason": reason, "actor": actor,
            "tripped_at": _now(), "reset_at": None,
            "paused_at": prior.get("paused_at"), "resumed_at": prior.get("resumed_at"),
        }
        self._write(data)
        self._broadcast("tripped", reason, actor, webhook_urls)
        return self.status()

    def pause(self, reason: str, actor: str, *, webhook_urls: tuple[str, ...] | None = None) -> SwitchStatus:
        """Sets the softer, distinct STATE_PAUSED - same auditability contract as trip() (reason
        and actor both required, both recorded), but does NOT set `tripped` - a duck-typed
        integration checking `tripped()` alone is correctly unaffected by a pause. Use `paused()`
        or `status().state` to observe this state."""
        if not reason.strip():
            raise ValueError("reason must not be empty - a kill switch pause with no reason is not auditable")
        if not actor.strip():
            raise ValueError("actor must not be empty - a kill switch pause with no actor is not auditable")
        prior = self._read()
        data = {
            **_DEFAULT_STATUS, "state": STATE_PAUSED, "tripped": False,
            "reason": reason, "actor": actor,
            "tripped_at": prior.get("tripped_at"), "reset_at": prior.get("reset_at"),
            "paused_at": _now(), "resumed_at": None,
        }
        self._write(data)
        self._broadcast("paused", reason, actor, webhook_urls)
        return self.status()

    def reset(self, actor: str, *, webhook_urls: tuple[str, ...] | None = None) -> SwitchStatus:
        """Clears the switch back to STATE_CLEAR from either TRIPPED or PAUSED - one clearing verb
        for both, since "resolved, back to normal" is the same action regardless of which
        non-clear state it's leaving. Records `resumed_at` (in addition to `reset_at`) when the
        prior state was specifically PAUSED, so the audit trail can tell the two cases apart."""
        if not actor.strip():
            raise ValueError("actor must not be empty - a kill switch reset with no actor is not auditable")
        prior = self._read()
        was_paused = prior.get("state") == STATE_PAUSED
        data = {
            **_DEFAULT_STATUS, "state": STATE_CLEAR, "tripped": False,
            "actor": actor,
            "tripped_at": prior.get("tripped_at"), "reset_at": _now(),
            "paused_at": prior.get("paused_at"),
            "resumed_at": _now() if was_paused else prior.get("resumed_at"),
        }
        self._write(data)
        self._broadcast("reset", prior.get("reason"), actor, webhook_urls)
        return self.status()

    def _broadcast(self, kind: str, reason: str | None, actor: str, webhook_urls: tuple[str, ...] | None) -> None:
        urls = webhook_urls if webhook_urls is not None else self.webhook_urls
        if not urls:
            return
        event = BroadcastEvent(kind=kind, reason=reason, actor=actor, at=_now())
        for url in urls:
            send_broadcast(url, event)
