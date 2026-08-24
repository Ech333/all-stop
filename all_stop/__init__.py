"""All-Stop - a self-hosted, file-backed kill switch and webhook broadcast for stopping agent
tooling org-wide, without any Northstar-operated infrastructure in the path.

See kill_switch.py and broadcast.py for the real mechanism and its honest scope.
"""

from .broadcast import BroadcastEvent, send_broadcast
from .kill_switch import KillSwitch, SwitchStatus

__version__ = "0.1.0"

__all__ = [
    "KillSwitch",
    "SwitchStatus",
    "BroadcastEvent",
    "send_broadcast",
    "__version__",
]
