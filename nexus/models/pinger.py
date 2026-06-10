"""Model pinger — parallel model health checks.

Pings all known models from a provider simultaneously using asyncio,
reports which are accessible (200) and which fail.

Used by the setup wizard to auto-discover working models if the
user's chosen model is unreachable.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PingResult:
    model_id: str
    status_code: int | None
    ok: bool
    latency_ms: float
    error: str = ""


@dataclass
class ProviderPingReport:
    provider: str
    api_key: str
    base_url: str | None
    results: list[PingResult] = field(default_factory=list)
    total_time_ms: float = 0.0

    @property
    def working(self) -> list[PingResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[PingResult]:
        return [r for r in self.results if not r.ok]


class ModelPinger:
    """Pings models from a provider in parallel to find accessible ones."""

    def __init__(self, concurrency: int = 10):
        self.concurrency = concurrency

    async def ping_provider(
        self,
        provider: str,
        api_key: str,
        models: list[str],
        base_url: str | None = None,
        timeout: float = 5.0,
    ) -> ProviderPingReport:
        """Ping all given models for a provider in parallel.

        Returns a report with per-model status codes and latency.
        """
        import httpx

        report = ProviderPingReport(
            provider=provider,
            api_key=api_key,
            base_url=base_url,
        )
        start = time.monotonic()

        sem = asyncio.Semaphore(self.concurrency)

        async def _ping_one(model: str) -> PingResult:
            async with sem:
                return await self._ping_single(
                    provider, model, api_key, base_url, timeout
                )

        tasks = [_ping_one(m) for m in models]
        report.results = await asyncio.gather(*tasks, return_exceptions=False)
        report.total_time_ms = (time.monotonic() - start) * 1000
        return report

    async def _ping_single(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str | None,
        timeout: float,
    ) -> PingResult:
        """Ping a single model and return the result."""
        import httpx

        start = time.monotonic()
        try:
            url, headers, payload, params = _build_request(
                provider, model, api_key, base_url
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers, params=params)

            latency = (time.monotonic() - start) * 1000
            ok = resp.status_code == 200
            return PingResult(
                model_id=model,
                status_code=resp.status_code,
                ok=ok,
                latency_ms=round(latency, 1),
                error="" if ok else _extract_error(resp),
            )
        except Exception as e:
            latency = (time.monotonic() - start) * 1000
            return PingResult(
                model_id=model,
                status_code=None,
                ok=False,
                latency_ms=round(latency, 1),
                error=str(e)[:100],
            )

    def ping_provider_sync(
        self,
        provider: str,
        api_key: str,
        models: list[str],
        base_url: str | None = None,
        timeout: float = 5.0,
    ) -> ProviderPingReport:
        """Synchronous wrapper."""
        return asyncio.run(
            self.ping_provider(provider, api_key, models, base_url, timeout)
        )


# =============================================================================
# Request builder
# =============================================================================


def _build_request(
    provider: str, model: str, api_key: str, base_url: str | None
) -> tuple[str, dict, dict, dict | None]:
    """Build URL, headers, payload, and optional params for a ping request.

    Uses a minimal 1-token ping message to test accessibility.
    """
    headers: dict[str, str] = {}
    payload: dict[str, Any] = {}
    params: dict[str, str] | None = None

    providers_openai_compat = {
        "openai", "groq", "openrouter", "deepseek",
        "together", "mistral", "opencode-zen", "opencode-go",
    }

    if provider in providers_openai_compat:
        base = base_url or _default_url(provider)
        url = f"{base.rstrip('/')}/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        headers["Content-Type"] = "application/json"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "stream": False,
        }
    elif provider == "anthropic":
        url = base_url or "https://api.anthropic.com/v1/messages"
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        headers["Content-Type"] = "application/json"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }
    elif provider == "google":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        params = {"key": api_key}
        payload = {"contents": [{"parts": [{"text": "hi"}]}]}
    elif provider == "ollama":
        url = f"{(base_url or 'http://localhost:11434').rstrip('/')}/api/chat"
        payload = {"model": model, "messages": [{"role": "user", "content": "hi"}], "stream": False}
    else:
        # Fallback to OpenAI-compatible
        url = f"{(base_url or 'https://api.openai.com/v1').rstrip('/')}/chat/completions"
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }

    return url, headers, payload, params


def _default_url(provider: str) -> str:
    urls = {
        "openai": "https://api.openai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "deepseek": "https://api.deepseek.com/v1",
        "together": "https://api.together.xyz/v1",
        "mistral": "https://api.mistral.ai/v1",
        "cohere": "https://api.cohere.ai/v1",
        "opencode-zen": "https://opencode.ai/zen/v1",
        "opencode-go": "https://opencode.ai/zen/go/v1",
        "ollama": "http://localhost:11434",
    }
    return urls.get(provider, "https://api.openai.com/v1")


def _extract_error(resp: Any) -> str:
    """Extract a readable error message from a response."""
    try:
        data = resp.json()
        err = data.get("error", {})
        if isinstance(err, dict):
            msg = err.get("message", "")
            if msg:
                return msg[:120]
        elif isinstance(err, str):
            return err[:120]
        return data.get("error_description", "")[:120] or f"HTTP {resp.status_code}"
    except Exception:
        return f"HTTP {resp.status_code}"
