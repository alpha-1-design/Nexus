"""Tests for provider-aware identity (Feature A).

Covers the brand-color map coverage over every registered provider, alias
resolution, the welcome card's pure selection function, the colored status
bar, and the TUI autocomplete surfaces for /skills.
"""

import re

import pytest

from nexus.tui.colors import (
    DEFAULT_BRAND_COLOR,
    PROVIDER_BRAND_COLORS,
    format_provider_badge,
    get_provider_brand_color,
    hex_to_ansi24,
    resolve_provider_key,
)
from nexus.tui.palette import CommandPalette
from nexus.tui.widgets import (
    SlashCommandDropdown,
    StatusBar,
    WelcomeCard,
    _detect_active_provider,
    resolve_welcome_identity,
)

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Every provider config.py can register from the environment / ~/.nexus/.env.
_ALL_KNOWN_PROVIDER_NAMES = [
    "openai",
    "anthropic",
    "google",
    "groq",
    "deepseek",
    "mistral",
    "openrouter",
    "cerebras",
    "github-models",
    "huggingface",
    "cloudflare-workers-ai",
    "zhipu",
    "opencode-zen",
    "opencode-go",
    "ollama",
]


class TestBrandColorMap:
    def test_map_is_valid_hex(self):
        for name, color in PROVIDER_BRAND_COLORS.items():
            assert HEX_RE.match(color), f"{name}: {color} is not #rrggbb"

    def test_all_registered_providers_have_brand_colors(self):
        """Every provider config.py registers must resolve to a brand color
        that differs from the neutral fallback."""
        for name in _ALL_KNOWN_PROVIDER_NAMES:
            color = get_provider_brand_color(name)
            assert color != DEFAULT_BRAND_COLOR, f"{name} fell back to neutral cyan"
            assert HEX_RE.match(color)

    def test_detect_env_providers_all_covered(self):
        """The env-detection layer (config.py) is the real registration
        source: with every credential present, every detected provider name
        must land on a brand color."""
        from nexus.config import _detect_env_providers

        env = {
            "OPENAI_API_KEY": "k",
            "ANTHROPIC_API_KEY": "k",
            "GEMINI_API_KEY": "k",
            "GROQ_API_KEY": "k",
            "DEEPSEEK_API_KEY": "k",
            "MISTRAL_API_KEY": "k",
            "OPENROUTER_API_KEY": "k",
            "CEREBRAS_API_KEY": "k",
            "GITHUB_TOKEN": "k",
            "HF_TOKEN": "k",
            "CLOUDFLARE_API_TOKEN": "k",
            "CLOUDFLARE_ACCOUNT_ID": "acct",
            "ZHIPUAI_API_KEY": "k",
            "OPENCODE_ZEN_API_KEY": "k",
            "OPENCODE_GO_API_KEY": "k",
            "OLLAMA_HOST": "http://localhost:11434",
        }
        detected = _detect_env_providers(environ=env)
        assert len(detected) >= 14
        for name in detected:
            assert get_provider_brand_color(name) != DEFAULT_BRAND_COLOR, (
                f"detected provider '{name}' has no brand color"
            )

    def test_alias_resolution(self):
        assert get_provider_brand_color("google") == get_provider_brand_color("gemini")
        assert get_provider_brand_color("github-models") == get_provider_brand_color("github")
        assert get_provider_brand_color("cloudflare-workers-ai") == get_provider_brand_color("cloudflare")
        assert get_provider_brand_color("zhipu") == get_provider_brand_color("glm")
        assert resolve_provider_key("GROQ") == "groq"  # case-insensitive

    def test_unknown_provider_falls_back_to_neutral_cyan(self):
        assert get_provider_brand_color("totally-unknown") == DEFAULT_BRAND_COLOR
        assert get_provider_brand_color("") == DEFAULT_BRAND_COLOR
        assert get_provider_brand_color(None) == DEFAULT_BRAND_COLOR
        assert resolve_provider_key("totally-unknown") is None

    def test_mission_examples(self):
        """Spot-check the exact brand tints for the headline providers."""
        assert get_provider_brand_color("groq") == "#f55036"
        assert get_provider_brand_color("openai") == "#10a37f"
        assert get_provider_brand_color("anthropic") == "#d97757"
        assert get_provider_brand_color("deepseek") == "#4d6bfe"
        assert get_provider_brand_color("huggingface") == "#ffd21e"


class TestBadgeHelpers:
    def test_badge_with_model(self):
        assert format_provider_badge("groq", "llama-3.3-70b") == "⬢ groq · llama-3.3-70b"

    def test_badge_without_model(self):
        assert format_provider_badge("groq") == "⬢ groq"
        assert format_provider_badge("groq", "") == "⬢ groq"

    def test_hex_to_ansi24(self):
        assert hex_to_ansi24("#00f2ff") == "\033[38;2;0;242;255m"
        assert hex_to_ansi24("#F55036") == "\033[38;2;245;80;54m"
        assert hex_to_ansi24("bogus") == ""
        assert hex_to_ansi24("#fff") == ""


class TestWelcomeIdentitySelection:
    """Unit tests for the pure selection function (no pixel rendering)."""

    def test_configured_provider_selects_brand_color_and_badge(self):
        color, badge = resolve_welcome_identity("groq", "llama-3.3-70b")
        assert color == "#f55036"
        assert badge == "⬢ groq · llama-3.3-70b"

    def test_alias_provider_selects_canonical_color(self):
        color, badge = resolve_welcome_identity("github-models", "openai/gpt-4o-mini")
        assert color == get_provider_brand_color("github")
        assert badge == "⬢ github-models · openai/gpt-4o-mini"

    def test_no_provider_keeps_neutral_cyan_without_badge(self):
        color, badge = resolve_welcome_identity("", "")
        assert color == DEFAULT_BRAND_COLOR
        assert badge is None

    def test_detect_active_provider_returns_pair(self):
        """The auto-detect helper returns a (provider, model) pair and never
        raises, whatever the config state is."""
        provider, model = _detect_active_provider()
        assert isinstance(provider, str)
        assert isinstance(model, str)

    @pytest.mark.asyncio
    async def test_welcome_card_renders_badge_for_provider(self):
        from textual.app import App

        card = WelcomeCard(version="9.9.9", provider="groq", model="llama-3.3-70b")

        class HarnessApp(App):
            def compose(self):
                yield card

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            badge = card.query_one(".welcome-badge")
            assert "⬢ groq" in str(badge.render())
            texts = "\n".join(str(c.render()) for c in card.children)
            assert "███╗" in texts  # logo art still present
            assert "9.9.9" in texts

    @pytest.mark.asyncio
    async def test_welcome_card_without_provider_has_no_badge(self, monkeypatch):
        from textual.app import App

        monkeypatch.setattr(
            "nexus.tui.widgets._detect_active_provider", lambda: ("", "")
        )
        card = WelcomeCard(version="9.9.9")

        class HarnessApp(App):
            def compose(self):
                yield card

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not card.query(".welcome-badge")
            texts = "\n".join(str(c.render()) for c in card.children)
            assert "9.9.9" in texts  # the rest of the card is intact


class TestStatusBarBrandColor:
    def test_provider_rendered_in_brand_color_with_dot(self):
        bar = StatusBar(version="1.0", provider="groq")
        rendered = bar.render()
        assert "#f55036" in rendered
        assert "●" in rendered
        assert "groq" in rendered

    def test_no_provider_stays_dim(self):
        bar = StatusBar(version="1.0")
        rendered = bar.render()
        assert "no provider" in rendered
        assert "#f55036" not in rendered

    def test_alias_provider_gets_canonical_color(self):
        bar = StatusBar(version="1.0", provider="github-models")
        assert "#58a6ff" in bar.render()


class TestSkillsAutocompleteSurfaces:
    def test_dropdown_lists_skills(self):
        names = [cmd for cmd, _ in SlashCommandDropdown.COMMANDS]
        assert "/skills" in names
        entry = next(desc for cmd, desc in SlashCommandDropdown.COMMANDS if cmd == "/skills")
        assert entry.strip()

    def test_palette_lists_skills(self):
        assert "/skills" in CommandPalette.COMMANDS

    def test_dropdown_filter_finds_skills(self):
        dropdown = SlashCommandDropdown(filter_text="/sk")
        dropdown._update_filtered()
        assert "/skills" in [cmd for cmd, _ in dropdown._filtered]
