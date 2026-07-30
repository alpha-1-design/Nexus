"""Shared pytest fixtures for the Nexus test suite.

Provides test isolation for module-level singletons (e.g. the SafetyEngine)
so that state set by one test file (such as CLI invocations in
test_commands.py) can't leak into unrelated tests.
"""

import pytest

from nexus.safety import SafetyMode, get_safety_engine


@pytest.fixture(autouse=True)
def _reset_safety_engine():
    """Reset the global SafetyEngine singleton to its default mode
    before and after every test, so tests remain order-independent.
    """
    engine = get_safety_engine()
    engine.set_mode(SafetyMode.USER_REVIEW)
    yield
    engine.set_mode(SafetyMode.USER_REVIEW)
