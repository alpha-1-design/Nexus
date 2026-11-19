"""Tests for the per-provider circuit breaker wired into ProviderManager."""

import asyncio

import pytest

from nexus.config import ProviderConfig
from nexus.providers.base import Response
from nexus.providers.manager import ProviderManager


class FakeProvider:
    """Provider stub that fails the first `fail_times` calls, then succeeds."""

    def __init__(self, fail_times: int = 0):
        self.calls = 0
        self.fail_times = fail_times

    async def complete(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("simulated failure")
        return Response(content="ok", model="fake")

    async def stream(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("simulated failure")
        return
        yield  # pragma: no cover — makes this an async generator


def make_manager() -> ProviderManager:
    mgr = ProviderManager()
    mgr.providers["a"] = FakeProvider(fail_times=99)  # always fails
    mgr.providers["b"] = FakeProvider()  # always succeeds
    for name in ("a", "b"):
        mgr.configs[name] = ProviderConfig(name=name, provider_type="openai", enabled=True)
    mgr.active_provider = "a"
    return mgr


@pytest.mark.asyncio
async def test_breaker_opens_after_three_consecutive_failures():
    mgr = make_manager()
    msg = [{"role": "user", "content": "hi"}]

    assert mgr.breaker_state("a") == "closed"
    for _ in range(3):
        await mgr.complete(msg, provider_name="a")  # a fails, rotates to b

    assert mgr.providers["a"].calls == 3
    assert mgr.breaker_state("a") == "open"


@pytest.mark.asyncio
async def test_open_provider_is_excluded_from_rotation():
    mgr = make_manager()
    msg = [{"role": "user", "content": "hi"}]
    for _ in range(3):
        await mgr.complete(msg, provider_name="a")

    # An open provider is skipped by rotation entirely: no further calls hit it.
    assert "a" not in mgr._rotation_candidates("a")
    resp = await mgr.complete(msg, provider_name="a")
    assert resp.content == "ok"
    assert mgr.providers["a"].calls == 3  # served by b, a untouched


@pytest.mark.asyncio
async def test_breaker_half_opens_and_single_probe_fails_reopens():
    mgr = make_manager()
    msg = [{"role": "user", "content": "hi"}]
    for _ in range(3):
        await mgr.complete(msg, provider_name="a")

    mgr.breaker_open_seconds = 0.05
    await asyncio.sleep(0.06)
    assert mgr.breaker_state("a") == "half_open"
    assert "a" in mgr._rotation_candidates("a")  # half-open admits one probe

    # The probe runs and fails → circuit re-opens (timeout re-armed).
    await mgr.complete(msg, provider_name="a")
    assert mgr.providers["a"].calls == 4
    assert mgr.breaker_state("a") == "open"


@pytest.mark.asyncio
async def test_success_resets_counter_and_closes_circuit():
    mgr = ProviderManager()
    mgr.providers["a"] = FakeProvider(fail_times=2)  # fails twice, then works
    mgr.providers["b"] = FakeProvider()
    for name in ("a", "b"):
        mgr.configs[name] = ProviderConfig(name=name, provider_type="openai", enabled=True)
    mgr.active_provider = "a"
    msg = [{"role": "user", "content": "hi"}]

    await mgr.complete(msg, provider_name="a")
    await mgr.complete(msg, provider_name="a")
    assert mgr.providers["a"].calls == 2

    # The third attempt succeeds: circuit closes and the counter clears.
    resp = await mgr.complete(msg, provider_name="a")
    assert resp.content == "ok"
    assert mgr.breaker_state("a") == "closed"
    assert "a" not in mgr._breaker_failures
    assert "a" not in mgr._breaker_opened_at

    # The counter really was reset: two fresh failures no longer open it.
    mgr.providers["a"] = FakeProvider(fail_times=99)
    await mgr.complete(msg, provider_name="a")
    await mgr.complete(msg, provider_name="a")
    assert mgr.breaker_state("a") == "closed"


@pytest.mark.asyncio
async def test_stream_failures_also_count_toward_breaker():
    mgr = make_manager()
    async for _chunk in mgr.stream([{"role": "user", "content": "hi"}], provider_name="a"):
        pass  # a fails at stream start, rotation hands off to b
    assert mgr.providers["a"].calls == 1

    mgr._breaker_failures.clear()  # start from a known counter state
    for _ in range(3):
        try:
            async for _chunk in mgr.stream([{"role": "user", "content": "hi"}], provider_name="a"):
                pass
        except RuntimeError:
            pass  # when every candidate fails the manager raises; a still counts
    assert mgr.breaker_state("a") == "open"
