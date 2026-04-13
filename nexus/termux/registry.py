"""Register Termux-specific tools."""

from ..tools.base import ToolRegistry
from ..termux.clipboard import ClipboardTool
from ..termux.notifications import NotificationTool


def register_termux_tools(registry: ToolRegistry) -> None:
    """Register Termux-specific tools."""
    registry.register(ClipboardTool())
    registry.register(NotificationTool())
