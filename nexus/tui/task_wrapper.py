"""
Nexus Task Wrapper - Unified Error and Status Management.
Ensures that all orchestrator tasks report status to the TUI.
"""

import logging
from collections.abc import Awaitable
from datetime import datetime
from typing import Any

from .state import ChatMessage, MessageRole


class OrchestrationTask:
    def __init__(self, tui_app: Any, task_func: Awaitable[Any]):
        self.tui = tui_app
        self.task = task_func

    async def run(self):
        """Execute task with global error handling and UI feedback."""
        try:
            self.tui.query_one("StatusBar").update(message="Brain: Thinking...")
            await self.task
            self.tui.query_one("StatusBar").update(message="Brain: Ready.")
        except Exception as e:
            logging.error(f"Orchestration Error: {e}")
            try:
                self.tui.query_one("StatusBar").update(message=f"Brain Error: {str(e)[:30]}...")
            except Exception:
                pass  # never let the error path itself take the app down
            # BUGFIX (recon M2-4): this used to pass a plain string to
            # ChatPanel.add_message, which expects a ChatMessage — so the
            # error handler itself crashed and the user saw nothing.
            try:
                self.tui.query_one("#chat-panel").add_message(ChatMessage(
                    role=MessageRole.SYSTEM,
                    content=f"SYSTEM ERROR: {e}",
                    timestamp=datetime.now(),
                ))
            except Exception:
                pass
