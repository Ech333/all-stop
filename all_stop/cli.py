"""All-Stop CLI - a thin wrapper around KillSwitch for terminal/script/CI use.

`trip`   - full stop. Trips the switch, records who and why, fires any configured webhooks.
`pause`  - a distinct, softer "hold for human review" state - not a full stop (see kill_switch.py
           for the real design rationale). Same auditability contract as trip: reason and actor
           both required, both recorded, webhooks fired.
`status` - prints the current state. Exit code distinguishes severity so scripts/CI can branch on
           it: 0 = clear, 1 = tripped, 2 = paused. `all-stop status` is usable as a script/CI gate
           the same way this portfolio's other `check`/`verify` commands are.
`reset`  - clears the switch back to normal from either tripped OR paused, records who cleared
           it, fires any configured webhooks. One clearing verb for both non-clear states - see
           KillSwitch.reset()'s own docstring for why.
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


def _cmd_pause(args: argparse.Namespace) -> int:
    switch = KillSwitch(args.evidence, webhook_urls=tuple(args.webhook or ()))
    try:
        status = switch.pause(args.reason, args.actor)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"all-stop: PAUSED by {status.actor} at {status.paused_at} - {status.reason}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    switch = KillSwitch(args.evidence)
    status = switch.status()
    if status.tripped:
        print(f"all-stop: TRIPPED by {status.actor} at {status.tripped_at} - {status.reason}")
        return 1
    if status.paused:
        print(f"all-stop: PAUSED by {status.actor} at {status.paused_at} - {status.reason}")
        return 2
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

    p_pause = sub.add_parser("pause", help="set the softer, distinct 'hold for review' state - not a full stop")
    p_pause.add_argument("--evidence", required=True, help="path to the kill-switch state file")
    p_pause.add_argument("--reason", required=True)
    p_pause.add_argument("--actor", required=True, help="who is pausing it - for the audit trail")
    p_pause.add_argument("--webhook", action="append", help="webhook URL to notify (repeatable)")
    p_pause.set_defaults(func=_cmd_pause)

    p_status = sub.add_parser(
        "status", help="print current state; exit code 0=clear, 1=tripped, 2=paused"
    )
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
