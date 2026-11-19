"""Provider manager - handles multiple AI providers with fallback and routing.

Adds FREE-TIER ROTATION: when the active provider rate-limits (HTTP 429,
quota/credit errors), it gets a short in-memory cooldown and the request
immediately rotates to the next free provider that has a key — paid providers
stay as a last resort. Callers can read ``last_rotation_note`` to surface the
switch in the UI.
"""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from ..config import ProviderConfig
from .base import (
    PROVIDER_INFO,
    PROVIDER_REGISTRY,
    BaseProvider,
    Message,
    OpenAIProvider,
    Response,
    StreamChunk,
)

# Providers that work without any API key.
NO_KEY_PROVIDERS = {"ollama"}

# Message fragments (lower-cased) that indicate a rate-limit / quota /
# out-of-credits error — the class of errors free rotation can fix.
RATE_LIMIT_MARKERS: tuple[str, ...] = (
    "429",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "quota",
    "resource_exhausted",
    "insufficient",
    "out of credits",
    "credit balance",
    "payment required",
)


@dataclass
class CostTracker:
    """Track usage costs per provider."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost: float = 0.0

    def add_usage(self, usage: dict[str, int], cost_per_million: dict[str, float]) -> None:
        if not usage:
            return
        self.requests += 1
        self.input_tokens += usage.get("prompt_tokens", 0)
        self.output_tokens += usage.get("completion_tokens", 0)

        input_cost = (usage.get("prompt_tokens", 0) / 1_000_000) * cost_per_million.get("input", 0)
        output_cost = (usage.get("completion_tokens", 0) / 1_000_000) * cost_per_million.get("output", 0)
        self.total_cost += input_cost + output_cost


class ProviderManager:
    """Manages multiple AI providers with free-tier rotation and fallback."""

    def __init__(self):
        self.providers: dict[str, BaseProvider] = {}
        self.configs: dict[str, ProviderConfig] = {}
        self.active_provider: str = "openai"
        self.cost_tracker: CostTracker = CostTracker()

        # --- Free-tier rotation state (in-memory only, resets on restart) ---
        self.cooldown_seconds: float = 120.0
        self._cooldowns: dict[str, float] = {}
        self.last_rotation_note: str | None = None
        self.on_rotation: Callable[[str], None] | None = None
        self.free_rotation: bool = True

        # Cost per million tokens (approximate)
        self.cost_rates: dict[str, dict[str, float]] = {
            "openai": {"input": 2.5, "output": 10.0},  # GPT-4o
            "anthropic": {"input": 3.0, "output": 15.0},  # Claude 3.5
            "google": {"input": 0.0, "output": 0.0},  # Free tier
            "groq": {"input": 0.0, "output": 0.0},  # Free tier
            "deepseek": {"input": 0.14, "output": 0.28},  # DeepSeek Coder
            "ollama": {"input": 0.0, "output": 0.0},  # Local
            "mistral": {"input": 0.0, "output": 0.0},  # Free experiment tier
            "openrouter": {"input": 0.0, "output": 0.0},  # :free models
            "cerebras": {"input": 0.0, "output": 0.0},  # Free tier
            "github-models": {"input": 0.0, "output": 0.0},  # Free tier
            "huggingface": {"input": 0.0, "output": 0.0},  # Free credits
            "cloudflare-workers-ai": {"input": 0.0, "output": 0.0},  # Free neurons
            "zhipu": {"input": 0.0, "output": 0.0},  # glm-4-flash free
            "opencode-zen": {"input": 0.0, "output": 0.0},  # Free
            "opencode-go": {"input": 0.0, "output": 0.0},  # Free
        }

    # ================================================================
    # Rotation helpers
    # ================================================================

    def _is_rate_limit_error(self, exc: BaseException) -> bool:
        """True for quota/rate-limit/credit errors that rotation can fix."""
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int) and status in (402, 429):
            return True
        message = str(exc).lower()
        return any(marker in message for marker in RATE_LIMIT_MARKERS)

    def _mark_cooldown(self, name: str, seconds: float | None = None) -> None:
        """Put a provider on cooldown (default 120s) after a rate limit."""
        self._cooldowns[name] = time.monotonic() + (seconds if seconds is not None else self.cooldown_seconds)

    def _cooldown_left(self, name: str) -> float:
        """Seconds left on a provider's cooldown (0.0 = available again)."""
        expires = self._cooldowns.get(name)
        if expires is None:
            return 0.0
        left = expires - time.monotonic()
        if left <= 0:
            self._cooldowns.pop(name, None)
            return 0.0
        return left

    def _is_free(self, name: str) -> bool:
        """Registry free-flag for a configured provider name."""
        cfg = self.configs.get(name)
        meta = PROVIDER_INFO.get(name)
        if meta is None and cfg is not None:
            meta = PROVIDER_INFO.get(cfg.provider_type)
        return bool(meta.free) if meta else False

    def _free_rank(self, name: str) -> int:
        """Position of a free provider in registry order (unknown last)."""
        return list(PROVIDER_INFO.keys()).index(name) if name in PROVIDER_INFO else len(PROVIDER_INFO)

    def _rotation_candidates(self, active: str) -> list[str]:
        """Candidate order: active first, then FREE providers with a key and
        no cooldown (registry order), then paid ones (last resort). Cooling
        providers are excluded until their cooldown expires."""
        names = [n for n in self.configs if self.configs[n].enabled]
        available = [n for n in names if self._cooldown_left(n) <= 0.0]
        head = [active] if active in available else []
        tail = [n for n in available if n != active]
        if not self.free_rotation:
            return head + tail
        free = sorted((n for n in tail if self._is_free(n)), key=self._free_rank)
        paid = [n for n in tail if not self._is_free(n)]
        return head + free + paid

    def _note_rotation(self, failed: str, target: str) -> None:
        """Record (and print) that rotation moved work to another provider."""
        config = self.configs.get(target)
        model = config.model if config else ""
        note = f"⚡ rate-limited on {failed} → rotated to {target}/{model}".rstrip("/")
        self.last_rotation_note = note
        print(f"  \033[33m⚡\033[0m {note}")
        if self.on_rotation:
            try:
                self.on_rotation(note)
            except Exception:
                pass

    def _handle_failure(self, name: str, exc: BaseException) -> None:
        """Classify one provider failure: cooldown on rate limits, else plain
        fallback message (legacy behaviour for non-rate-limit errors)."""
        cyan, blue, dim, reset, yellow = "\033[36m", "\033[34m", "\033[90m", "\033[0m", "\033[33m"
        if self._is_rate_limit_error(exc):
            self._mark_cooldown(name)
            print(
                f"  {blue}╼{reset} {dim}nexus/providers{reset} {cyan}{name}{reset} "
                f"{yellow}rate-limited — cooling down {self.cooldown_seconds:.0f}s{reset}"
            )
            remaining = [n for n in self._rotation_candidates(self.active_provider) if n != name]
            if remaining:
                self._note_rotation(name, remaining[0])
            return
        print(f"  {blue}╼{reset} {dim}nexus/providers{reset} {cyan}{name}{reset} {dim}failed. Switching fallback...{reset}")

    def provider_status(self) -> list[dict[str, Any]]:
        """Per-provider status for UIs: name, free flag, configured, cooldown."""
        statuses: list[dict[str, Any]] = []
        for name, config in self.configs.items():
            meta = PROVIDER_INFO.get(name) or PROVIDER_INFO.get(config.provider_type)
            statuses.append(
                {
                    "name": name,
                    "free": bool(meta.free) if meta else False,
                    "configured": bool(self._resolve_api_key(config)) or config.provider_type in NO_KEY_PROVIDERS,
                    "active": name == self.active_provider,
                    "enabled": config.enabled,
                    "model": config.model,
                    "cooldown_seconds_left": round(self._cooldown_left(name), 1),
                }
            )
        return statuses

    # ================================================================
    # Provider configuration / lifecycle
    # ================================================================

    def add_provider(self, config: ProviderConfig) -> None:
        """Add a provider configuration."""
        self.configs[config.name] = config

    def get_provider_config(self, name: str) -> ProviderConfig | None:
        """Get provider configuration by name."""
        return self.configs.get(name)

    def set_active(self, name: str) -> None:
        """Set the active provider."""
        if name not in self.providers:
            config = self.configs.get(name)
            if not config:
                raise ValueError(f"Provider '{name}' not found. Add it first with provider add.")
            self.providers[name] = self._create_provider(config)
        self.active_provider = name

    def _resolve_api_key(self, config: ProviderConfig) -> str:
        """Config key first, then the registry env keys (global .env included)."""
        if config.api_key:
            return config.api_key
        meta = PROVIDER_INFO.get(config.provider_type) or PROVIDER_INFO.get(config.name)
        if meta:
            for env_name in meta.env_keys:
                value = os.environ.get(env_name)
                if value:
                    return value
        return ""

    def _create_provider(self, config: ProviderConfig) -> BaseProvider:
        """Create a provider instance from config."""
        provider_config = {
            "api_key": self._resolve_api_key(config),
            "base_url": config.base_url,
            "model": config.model,
            "timeout": config.timeout,
        }
        provider_cls = PROVIDER_REGISTRY.get(config.provider_type) or PROVIDER_REGISTRY.get(config.name)
        if provider_cls is None:
            provider_cls = OpenAIProvider  # last resort: OpenAI-compatible
        return provider_cls(provider_config)

    async def get_provider(self, name: str | None = None) -> BaseProvider:
        """Get a provider instance, creating if needed."""
        name = name or self.active_provider

        if name not in self.providers:
            config = self.configs.get(name)
            if not config:
                raise ValueError(f"Provider '{name}' not found")
            self.providers[name] = self._create_provider(config)

        return self.providers[name]

    # ================================================================
    # LLM entry points
    # ================================================================

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        provider_name: str | None = None,
        **kwargs,
    ) -> Response:
        """Generate a completion with free-tier rotation and fallback.

        Candidates: active provider, then free providers with a key and no
        cooldown (registry order), then paid ones. A rate-limit/quota error
        puts the provider on a cooldown and the next candidate is tried
        immediately; ``last_rotation_note`` describes the switch for UIs.
        """
        self.last_rotation_note = None
        active = provider_name or self.active_provider
        provider_names = self._rotation_candidates(active)

        max_retries = 2

        for name in provider_names:
            for attempt in range(max_retries + 1):
                try:
                    provider = await self.get_provider(name)
                    response = await provider.complete(messages, tools, **kwargs)

                    # Track costs
                    if response.usage:
                        rates = self.cost_rates.get(name, {"input": 0, "output": 0})
                        self.cost_tracker.add_usage(response.usage, rates)

                    return response
                except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                    if attempt < max_retries:
                        wait = (attempt + 1) * 2
                        print(f"  \033[34m╼\033[0m \033[90mnexus/providers\033[0m \033[33mNetwork error on {name}, retrying in {wait}s...\033[0m")
                        await asyncio.sleep(wait)
                        continue
                    self._handle_failure(name, e)
                    break  # Exhausted retries for this provider
                except Exception as e:
                    self._handle_failure(name, e)
                    break

        raise RuntimeError("All providers failed. See logs for details.")

    async def stream(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        provider_name: str | None = None,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a completion with free-tier rotation and fallback.

        Fallback happens at stream START only: while nothing has been emitted,
        a failure moves to the next provider. Once a chunk has been emitted a
        mid-stream failure is re-raised instead of restarting, so consumers
        never receive duplicated output.
        """
        self.last_rotation_note = None
        active = provider_name or self.active_provider
        provider_names = self._rotation_candidates(active)

        cyan = "\033[36m"
        blue = "\033[34m"
        dim = "\033[90m"
        reset = "\033[0m"
        yellow = "\033[33m"

        last_error = None
        for name in provider_names:
            emitted = False
            try:
                provider = await self.get_provider(name)
                async for chunk in provider.stream(messages, tools, **kwargs):
                    emitted = True
                    yield chunk
                return
            except Exception as e:
                last_error = e
                if emitted:
                    # Output already reached the consumer — restarting another
                    # provider would duplicate it. Fail cleanly instead.
                    raise
                if self._is_rate_limit_error(e):
                    self._mark_cooldown(name)
                    print(
                        f"  {blue}╼{reset} {dim}nexus/providers{reset} {cyan}{name}{reset} "
                        f"{yellow}rate-limited — cooling down {self.cooldown_seconds:.0f}s{reset}"
                    )
                    remaining = [n for n in provider_names if n != name and self._cooldown_left(n) <= 0.0]
                    if remaining:
                        self._note_rotation(name, remaining[0])
                else:
                    print(f"  {blue}╼{reset} {dim}nexus/providers{reset} {cyan}{name}{reset} {dim}stream failure. Switching fallback...{reset}")
                continue

        # If we get here, all failed
        raise RuntimeError(f"All providers failed to stream. Last error: {last_error}")

    async def parallel_complete(
        self,
        tasks: list[dict[str, Any]],  # List of {"messages": [], "tools": [], "provider_name": str}
        **kwargs,
    ) -> list[Response | Exception]:
        """Execute multiple completions in parallel using different providers/keys."""

        async def _run_task(task_data: dict[str, Any]) -> Response:
            return await self.complete(messages=task_data["messages"], tools=task_data.get("tools"), provider_name=task_data.get("provider_name"), **kwargs)

        return await asyncio.gather(*[_run_task(t) for t in tasks], return_exceptions=True)

    def get_enabled_providers(self) -> list[str]:
        """Get names of all enabled providers."""
        return [name for name, cfg in self.configs.items() if cfg.enabled]

    async def switch_model(self, model: str, provider_name: str | None = None) -> None:
        """Switch the model for a provider."""
        name = provider_name or self.active_provider
        config = self.configs.get(name)
        if not config:
            raise ValueError(f"Provider '{name}' not found")

        config.model = model

        # Recreate the provider with new model
        if name in self.providers:
            await self.providers[name].close()
            self.providers[name] = self._create_provider(config)

    def get_stats(self) -> dict[str, Any]:
        """Get provider statistics."""
        return {
            "active_provider": self.active_provider,
            "total_requests": self.cost_tracker.requests,
            "total_input_tokens": self.cost_tracker.input_tokens,
            "total_output_tokens": self.cost_tracker.output_tokens,
            "total_cost": self.cost_tracker.total_cost,
            "providers": {
                name: {
                    "model": config.model,
                    "provider_type": config.provider_type,
                    "enabled": config.enabled,
                    "free": self._is_free(name),
                    "cooldown_seconds_left": round(self._cooldown_left(name), 1),
                }
                for name, config in self.configs.items()
            },
        }

    async def close_all(self) -> None:
        """Close all provider connections."""
        for provider in self.providers.values():
            await provider.close()


# Global provider manager instance
_manager: ProviderManager | None = None


def get_manager() -> ProviderManager:
    """Get the global provider manager instance."""
    global _manager
    if _manager is None:
        _manager = ProviderManager()
        _load_config_into_manager(_manager)
    return _manager


def _load_config_into_manager(manager: ProviderManager) -> None:
    """Load provider configurations from config file."""
    try:
        from ..config import load_config

        cfg = load_config()

        for name, prov_config in cfg.providers.items():
            from ..config import ProviderConfig

            manager.configs[name] = ProviderConfig(
                name=prov_config.name,
                provider_type=prov_config.provider_type,
                api_key=prov_config.api_key or "",
                base_url=prov_config.base_url,
                model=prov_config.model,
                max_tokens=prov_config.max_tokens,
                temperature=prov_config.temperature,
                timeout=prov_config.timeout,
                enabled=prov_config.enabled,
                env_sourced=getattr(prov_config, "env_sourced", False),
            )

        if cfg.active_provider and cfg.active_provider in manager.configs:
            manager.active_provider = cfg.active_provider
        manager.free_rotation = getattr(cfg, "free_rotation", True)
    except Exception:
        pass


async def reset_manager() -> None:
    """Reset the global provider manager."""
    global _manager
    if _manager:
        await _manager.close_all()
    _manager = None
