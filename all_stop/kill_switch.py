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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SwitchStatus:
    tripped: bool
    reason: str | None
    actor: str | None
    tripped_at: str | None
    reset_at: str | None


_DEFAULT_STATUS = {
    "tripped": False,
    "reason": None,
    "actor": None,
    "tripped_at": None,
    "reset_at": None,
}


class KillSwitch:
    """One kill switch, backed by one JSON file at `path`.

    Every call to `tripped()` reads the file fresh from disk - no caching, no polling interval to
    tune, no daemon to keep alive. The cost is one small file read per check, which is the right
    trade for "a human needs this to actually work under pressure" over "this is the fastest
    possible check."""

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
            # not a spurious stop.
            return {**_DEFAULT_STATUS, "tripped": True, "reason": "state file unreadable/corrupt - failing closed"}
        merged = dict(_DEFAULT_STATUS)
        merged.update(data)
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
        return bool(self._read()["tripped"])

    def status(self) -> SwitchStatus:
        d = self._read()
        return SwitchStatus(
            tripped=bool(d["tripped"]), reason=d["reason"], actor=d["actor"],
            tripped_at=d["tripped_at"], reset_at=d["reset_at"],
        )

    def trip(self, reason: str, actor: str, *, webhook_urls: tuple[str, ...] | None = None) -> SwitchStatus:
        if not reason.strip():
            raise ValueError("reason must not be empty - a kill switch trip with no reason is not auditable")
        if not actor.strip():
            raise ValueError("actor must not be empty - a kill switch trip with no actor is not auditable")
        data = {
            "tripped": True, "reason": reason, "actor": actor,
            "tripped_at": _now(), "reset_at": None,
        }
        self._write(data)
        self._broadcast("tripped", reason, actor, webhook_urls)
        return self.status()

    def reset(self, actor: str, *, webhook_urls: tuple[str, ...] | None = None) -> SwitchStatus:
        if not actor.strip():
            raise ValueError("actor must not be empty - a kill switch reset with no actor is not auditable")
        prior = self._read()
        data = {
            "tripped": False, "reason": None, "actor": actor,
            "tripped_at": prior.get("tripped_at"), "reset_at": _now(),
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
