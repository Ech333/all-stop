"""All-Stop CLI - a thin wrapper around KillSwitch for terminal/script/CI use.

`trip`   - trips the switch, records who and why, fires any configured webhooks.
`status` - prints the current state. Exits non-zero while tripped, so `all-stop status` is
           usable as a script/CI gate the same way this portfolio's other `check`/`verify`
           commands are.
`reset`  - clears the switch, records who cleared it, fires any configured webhooks.
"""

from __future__ import annotations

import argparse
import sys

from .kill_switch import KillSwitch


def _cmd_trip(args: argparse.Namespace) -> int:
    switch = KillSwitch(args.evidence, webhook_urls=tuple(args.webhook or ()))
    try:
        status = switch.trip(args.reason, args.actor)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"all-stop: TRIPPED by {status.actor} at {status.tripped_at} - {status.reason}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    switch = KillSwitch(args.evidence)
    status = switch.status()
    if status.tripped:
        print(f"all-stop: TRIPPED by {status.actor} at {status.tripped_at} - {status.reason}")
        return 1
    print("all-stop: clear")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    switch = KillSwitch(args.evidence, webhook_urls=tuple(args.webhook or ()))
    try:
        status = switch.reset(args.actor)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"all-stop: cleared by {status.actor} at {status.reset_at}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="all-stop",
        description="A file-backed, multi-process kill switch and webhook broadcast for stopping agent tooling org-wide.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_trip = sub.add_parser("trip", help="trip the switch")
    p_trip.add_argument("--evidence", required=True, help="path to the kill-switch state file")
    p_trip.add_argument("--reason", required=True)
    p_trip.add_argument("--actor", required=True, help="who is tripping it - for the audit trail")
    p_trip.add_argument("--webhook", action="append", help="webhook URL to notify (repeatable)")
    p_trip.set_defaults(func=_cmd_trip)

    p_status = sub.add_parser("status", help="print current state; exits non-zero while tripped")
    p_status.add_argument("--evidence", required=True)
    p_status.set_defaults(func=_cmd_status)

    p_reset = sub.add_parser("reset", help="clear the switch")
    p_reset.add_argument("--evidence", required=True)
    p_reset.add_argument("--actor", required=True)
    p_reset.add_argument("--webhook", action="append", help="webhook URL to notify (repeatable)")
    p_reset.set_defaults(func=_cmd_reset)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
