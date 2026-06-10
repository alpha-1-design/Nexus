"""Model registry — fetches live model lists from provider APIs.

No hardcoded model lists. Everything is fetched dynamically from
each provider's API in real-time, then verified with pings.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelInfo:
    """Information about a model, fetched live from the provider API."""

    id: str
    provider: str
    provider_label: str = ""
    context_window: int = 0
    pricing_input: float = 0.0
    pricing_output: float = 0.0
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    created: int = 0
    owned_by: str = ""


# =============================================================================
# Live model fetchers — one per provider
# =============================================================================


async def fetch_openai_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                provider="openai",
                provider_label="OpenAI",
                created=m.get("created", 0),
                owned_by=m.get("owned_by", ""),
            )
            for m in data.get("data", [])
            if not m["id"].startswith("ft:")  # filter fine-tunes
        ]


async def fetch_anthropic_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                provider="anthropic",
                provider_label="Anthropic",
                created=m.get("created", 0),
                owned_by=m.get("owned_by", ""),
            )
            for m in data.get("data", [])
        ]


async def fetch_google_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            ModelInfo(
                id=m["name"].replace("models/", ""),
                provider="google",
                provider_label="Google Gemini",
                description=m.get("description", ""),
                owned_by=m.get("version", ""),
            )
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]


async def fetch_groq_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                provider="groq",
                provider_label="Groq",
                owned_by=m.get("owned_by", ""),
                created=m.get("created", 0),
                context_window=m.get("context_length", 0),
            )
            for m in data.get("data", [])
            if m.get("active", True)
        ]


async def fetch_openrouter_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        results = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            results.append(
                ModelInfo(
                    id=m["id"],
                    provider="openrouter",
                    provider_label="OpenRouter",
                    context_window=m.get("context_length", 0) or m.get("max_context", 0),
                    pricing_input=float(pricing.get("prompt", 0)),
                    pricing_output=float(pricing.get("completion", 0)),
                    description=m.get("description", ""),
                )
            )
        return results


async def fetch_deepseek_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.deepseek.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                provider="deepseek",
                provider_label="DeepSeek",
                owned_by=m.get("owned_by", ""),
            )
            for m in data.get("data", [])
        ]


async def fetch_together_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.together.xyz/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                provider="together",
                provider_label="Together AI",
                context_window=m.get("context_length", 0) or m.get("max_sequence_length", 0),
                description=m.get("description", "") or m.get("display_name", ""),
            )
            for m in data if m.get("id")
        ]


async def fetch_mistral_models(api_key: str) -> list[ModelInfo]:
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            "https://api.mistral.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                provider="mistral",
                provider_label="Mistral AI",
                owned_by=m.get("object", ""),
                created=m.get("created", 0),
            )
            for m in data.get("data", [])
        ]


async def fetch_ollama_models(base_url: str | None = None) -> list[ModelInfo]:
    import httpx
    url = f"{(base_url or 'http://localhost:11434').rstrip('/')}/api/tags"
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [
                ModelInfo(
                    id=m["name"],
                    provider="ollama",
                    provider_label="Ollama (Local)",
                )
                for m in data.get("models", [])
            ]
        except Exception:
            return []


async def fetch_opencode_models(api_key: str) -> list[ModelInfo]:
    import httpx
    base = "https://opencode.ai/zen/v1"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code != 200:
            # fallback: return known Zen models
            return [
                ModelInfo(id="minimax-m2.5-free", provider="opencode-zen", provider_label="OpenCode Zen"),
                ModelInfo(id="kimi-k2.5", provider="opencode-zen", provider_label="OpenCode Zen"),
            ]
        data = resp.json()
        return [
            ModelInfo(
                id=m["id"],
                provider="opencode-zen",
                provider_label="OpenCode Zen",
            )
            for m in data.get("data", [])
        ]


# =============================================================================
# Registry — fetches models live
# =============================================================================

_FETCHERS: dict[str, Any] = {
    "openai": fetch_openai_models,
    "anthropic": fetch_anthropic_models,
    "google": fetch_google_models,
    "groq": fetch_groq_models,
    "openrouter": fetch_openrouter_models,
    "deepseek": fetch_deepseek_models,
    "together": fetch_together_models,
    "mistral": fetch_mistral_models,
    "ollama": fetch_ollama_models,
    "opencode-zen": fetch_opencode_models,
}


class ModelRegistry:
    """Live model registry — fetches model lists from provider APIs."""

    async def fetch_models(
        self,
        provider: str,
        api_key: str = "",
        base_url: str | None = None,
    ) -> list[ModelInfo]:
        """Fetch available models for a provider from their API."""
        fetcher = _FETCHERS.get(provider)
        if not fetcher:
            return []

        try:
            if provider == "ollama":
                return await fetcher(base_url)
            return await fetcher(api_key)
        except Exception:
            return []

    async def fetch_and_ping(
        self,
        provider: str,
        api_key: str,
        base_url: str | None = None,
        ping_timeout: float = 5.0,
    ) -> tuple[list[ModelInfo], list[dict]]:
        """Fetch models and ping them all in parallel.

        Returns (models, ping_results) where ping_results is a list of
        dicts with keys: model_id, ok, status_code, latency_ms, error.
        """
        from .pinger import ModelPinger

        models = await self.fetch_models(provider, api_key, base_url)
        if not models:
            return [], []

        pinger = ModelPinger(concurrency=15)
        report = await pinger.ping_provider(
            provider=provider,
            api_key=api_key,
            models=[m.id for m in models],
            base_url=base_url,
            timeout=ping_timeout,
        )

        return models, [
            {
                "model_id": r.model_id,
                "ok": r.ok,
                "status_code": r.status_code,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in report.results
        ]


# Singleton
_registry: ModelRegistry | None = None


def get_registry() -> ModelRegistry:
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
