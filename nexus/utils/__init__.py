"""Utilities for Nexus."""

import asyncio
<<<<<<< HEAD
import sys
from typing import Any, Callable, TypeVar
=======
import logging
import re
from typing import Any, TypeVar
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)

T = TypeVar("T")


<<<<<<< HEAD
=======
def get_logger(name: str) -> logging.Logger:
    """Get a configured logger instance."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)
    return logger


>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
def run_async(coro: Any) -> Any:
    """Run an async function in a sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
<<<<<<< HEAD
=======

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    else:
        return asyncio.run(coro)


def format_bytes(size: int) -> str:
    """Format bytes as human-readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


def format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"


def truncate(text: str, length: int, suffix: str = "...") -> str:
    """Truncate text to a maximum length."""
    if len(text) <= length:
        return text
    return text[: length - len(suffix)] + suffix


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return singular or plural form based on count."""
    if count == 1:
        return singular
    return plural or singular + "s"
<<<<<<< HEAD
=======


SENSITIVE_PATTERNS = [
    (
        re.compile(r'([a-zA-Z0-9_-]+[_-]?key["\']?\s*[:=]\s*["\']?)([a-zA-Z0-9_\-]{8,})', re.I),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(bearer\s+)([a-zA-Z0-9_\-\.]{10,})", re.I), r"\1[REDACTED]"),
    (re.compile(r"(ghp_[a-zA-Z0-9]{36})"), "[GITHUB_TOKEN]"),
    (re.compile(r"(sk-[a-zA-Z0-9]{20,})"), "[API_KEY]"),
    (re.compile(r"(xai-[a-zA-Z0-9_-]{20,})"), "[XAI_KEY]"),
    (re.compile(r"/home/[a-zA-Z0-9_]+/"), "/home/[USER]/"),
    (re.compile(r"C:\\Users\\[a-zA-Z0-9_]+\\"), "C:\\Users\\[USER]\\"),
]


def sanitize_error(error: str | Exception, max_length: int = 200) -> str:
    """Sanitize an error message to prevent information disclosure.

    Removes or redacts:
    - API keys and tokens
    - File paths with usernames
    - Bearer tokens
    """
    if isinstance(error, Exception):
        error = str(error)

    sanitized = error
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "..."

    return sanitized
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
