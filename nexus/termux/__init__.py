"""Termux integration for Nexus.

Provides access to Termux API tools (notifications, clipboard, sensors, etc.)
when running on Android/Termux. Gracefully degrades on other platforms.
"""

from .api import TermuxAPI, get_termux_api
from .clipboard import ClipboardTool
from .notifications import NotificationTool
from .battery import BatteryStatus

__all__ = [
    "TermuxAPI",
    "get_termux_api", 
    "ClipboardTool",
    "NotificationTool",
    "BatteryStatus",
]
