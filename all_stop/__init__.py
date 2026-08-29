"""All-Stop - a self-hosted, file-backed kill switch and webhook broadcast for stopping agent
tooling org-wide, without any Northstar-operated infrastructure in the path.

See kill_switch.py and broadcast.py for the real mechanism and its honest scope.
"""

from .broadcast import BroadcastEvent, send_broadcast
from .kill_switch import STATE_CLEAR, STATE_PAUSED, STATE_TRIPPED, KillSwitch, SwitchStatus

__version__ = "0.2.0"

__all__ = [
    "KillSwitch",
    "SwitchStatus",
    "STATE_CLEAR",
    "STATE_PAUSED",
    "STATE_TRIPPED",
    "BroadcastEvent",
    "send_broadcast",
    "__version__",
]
