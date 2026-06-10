"""Voice engine for Nexus voice mode."""

from __future__ import annotations

from typing import Any


class VoiceEngine:
    """Voice interaction engine (placeholder — install voice extras to enable)."""

    def __init__(self, **kwargs: Any) -> None:
        self._running = False
        self._kwargs = kwargs

    async def voice_mode(self) -> VoiceEngine:
        self._running = True
        return self

    async def __aenter__(self) -> VoiceEngine:
        self._running = True
        return self

    async def __aexit__(self, *args: Any) -> None:
        self._running = False


def get_voice_engine(**overrides: Any) -> VoiceEngine:
    return VoiceEngine(**overrides)


def list_tts_voices() -> list[dict[str, str]]:
    return [{"name": "default", "locale": "en-US"}]
