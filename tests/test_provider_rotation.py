"""Tests for free-provider rotation in ProviderManager."""

import asyncio
import time

import httpx

from nexus.config import ProviderConfig
from nexus.providers.base import Message, Response, StreamChunk
from nexus.providers.manager import ProviderManager

MSG = [Message(role="user", content="hi")]


def _rate_limit_error() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(429, request=request, json={"error": {"message": "Rate limit reached"}})
    return httpx.HTTPStatusError(
        "Client error '429 Too Many Requests'", request=request, response=response
    )


class FakeProvider:
    """Scripted provider: each call consumes the next outcome (Response or Exception)."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def _next(self):
        self.calls += 1
        item = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    async def complete(self, messages, tools=None, **kwargs):
        return self._next()

    async def stream(self, messages, tools=None, **kwargs):
        self._next()
        yield StreamChunk(content="ok", done=True)

    async def close(self):
        pass


def _resp(model="fallback-model"):
    return Response(content="hello", model=model, finish_reason="stop")


def _cfg(name, ptype, key=None, model=None, enabled=True):
    return ProviderConfig(
        name=name,
        provider_type=ptype,
        api_key="key-" + name if key is None else key,
        model="model-" + name if model is None else model,
        enabled=enabled,
    )


def _manager(configs, providers) -> ProviderManager:
    m = ProviderManager()
    for c in configs:
        m.add_provider(c)
    for name, prov in providers.items():
        m.providers[name] = prov  # inject fakes so nothing hits the network
    return m


class TestRotation:
    def test_rate_limit_rotates_and_sets_note(self):
        m = _manager(
            [_cfg("groq", "groq"), _cfg("openrouter", "openrouter")],
            {
                "groq": FakeProvider([_rate_limit_error()]),
                "openrouter": FakeProvider([_resp("or-model")]),
            },
        )
        m.active_provider = "groq"

        resp = asyncio.run(m.complete(MSG))

        assert resp.model == "or-model"
        assert m.last_rotation_note is not None
        assert "groq" in m.last_rotation_note
        assert "openrouter" in m.last_rotation_note
        assert "⚡" in m.last_rotation_note
        assert m._cooldown_left("groq") > 0  # rate-limited provider is cooling down

    def test_free_pool_ordering(self):
        m = _manager(
            [
                _cfg("openai", "openai"),
                _cfg("groq", "groq"),
                _cfg("openrouter", "openrouter"),
                _cfg("anthropic", "anthropic"),
            ],
            {},
        )
        m.active_provider = "openai"

        candidates = m._rotation_candidates("openai")

        assert candidates[0] == "openai"  # active first
        # Free providers (registry order) before paid ones.
        assert candidates.index("groq") < candidates.index("anthropic")
        assert candidates.index("openrouter") < candidates.index("anthropic")
        assert candidates.index("groq") < candidates.index("openrouter")

    def test_cooling_provider_skipped_then_reenters(self):
        m = _manager([_cfg("groq", "groq"), _cfg("openrouter", "openrouter")], {})
        m.active_provider = "groq"

        m._mark_cooldown("groq", seconds=120)
        assert "groq" not in m._rotation_candidates("groq")
        status = {s["name"]: s for s in m.provider_status()}
        assert status["groq"]["cooldown_seconds_left"] > 0

        # Simulate cooldown expiry: the provider re-enters the pool automatically.
        m._cooldowns["groq"] = time.monotonic() - 1
        assert m._cooldown_left("groq") == 0.0
        assert "groq" in m._rotation_candidates("groq")

    def test_no_rotation_note_on_non_rate_limit_error(self):
        m = _manager(
            [_cfg("openai", "openai"), _cfg("groq", "groq")],
            {
                "openai": FakeProvider([ValueError("bad request")]),
                "groq": FakeProvider([_resp("groq-model")]),
            },
        )
        m.active_provider = "openai"

        resp = asyncio.run(m.complete(MSG))

        assert resp.model == "groq-model"  # ordinary fallback still works
        assert m.last_rotation_note is None  # but no rotation note
        assert m._cooldown_left("openai") == 0.0  # and no cooldown

    def test_stream_rotation_on_rate_limit(self):
        async def _run():
            m = _manager(
                [_cfg("groq", "groq"), _cfg("openrouter", "openrouter")],
                {
                    "groq": FakeProvider([_rate_limit_error()]),
                    "openrouter": FakeProvider([_resp()]),
                },
            )
            m.active_provider = "groq"
            chunks = [c async for c in m.stream(MSG)]
            return m, chunks

        m, chunks = asyncio.run(_run())
        assert chunks and chunks[-1].done
        assert m.last_rotation_note is not None
        assert "openrouter" in m.last_rotation_note

    def test_provider_status_fields(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        m = _manager(
            [_cfg("groq", "groq"), _cfg("openai", "openai", key="")],
            {},
        )
        m.active_provider = "groq"

        status = {s["name"]: s for s in m.provider_status()}

        assert status["groq"]["free"] is True
        assert status["groq"]["configured"] is True
        assert status["groq"]["active"] is True
        assert status["openai"]["free"] is False
        assert status["openai"]["configured"] is False  # no key anywhere
        assert status["groq"]["cooldown_seconds_left"] == 0.0

    def test_message_markers_detect_quota_variants(self):
        m = ProviderManager()
        assert m._is_rate_limit_error(_rate_limit_error()) is True
        assert m._is_rate_limit_error(RuntimeError("You exceeded your quota")) is True
        assert m._is_rate_limit_error(RuntimeError("insufficient credits")) is True
        assert m._is_rate_limit_error(ValueError("bad request")) is False
        assert m._is_rate_limit_error(RuntimeError("context length exceeded")) is False
