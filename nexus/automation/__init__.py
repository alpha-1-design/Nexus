"""Nexus Automation - Browser and API automation for web tasks."""

from .browser import (
    BrowserAutomation, BrowserManager, BrowserConfig,
    get_browser_manager, is_browser_available,
)
from .api_client import ApiAutomation, ApiFlow

__all__ = [
    "BrowserAutomation",
    "BrowserManager",
    "BrowserConfig",
    "get_browser_manager",
    "is_browser_available",
    "ApiAutomation",
    "ApiFlow",
]
