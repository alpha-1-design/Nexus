"""Tests for the TUI theme system — five built-ins, full token coverage,
live in-place swaps that re-render widgets without a restart."""

import pytest

from nexus.tui.colors import (
    CSS_COLORS,
    DEFAULT_THEME,
    REQUIRED_TOKENS,
    THEMES,
    THEME_NAMES,
    apply_theme,
    cycle_theme,
    get_current_theme,
    theme_token,
)


@pytest.fixture(autouse=True)
def _restore_theme():
    """Theme swaps are process-global — restore the incoming theme after
    every test so neighboring test files see their expected palette."""
    before = get_current_theme()
    yield
    apply_theme(before if before in THEME_NAMES else DEFAULT_THEME)


class TestThemeRegistry:
    @pytest.mark.parametrize("theme_name", THEME_NAMES)
    def test_every_theme_defines_every_token(self, theme_name):
        tokens = THEMES[theme_name]
        missing = REQUIRED_TOKENS - set(tokens)
        assert not missing, f"theme '{theme_name}' is missing: {sorted(missing)}"
        for key, value in tokens.items():
            assert isinstance(value, str) and value.startswith("#"), (
                f"{theme_name}.{key} = {value!r} is not a hex color"
            )

    @pytest.mark.parametrize("theme_name", THEME_NAMES)
    def test_diff_colors_stay_semantic_in_every_theme(self, theme_name):
        """+ lines must stay green-family and - lines red-family in ALL
        themes (tinted to fit, never semantically swapped)."""
        tokens = THEMES[theme_name]

        def _channels(hex_color: str) -> tuple[int, int, int]:
            value = hex_color.lstrip("#")
            return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]

        add_r, add_g, add_b = _channels(tokens["diff_add"])
        del_r, del_g, del_b = _channels(tokens["diff_del"])
        assert add_g > add_r and add_g >= add_b, (
            f"{theme_name} diff_add {tokens['diff_add']} reads as green"
        )
        assert del_r > del_g and del_r >= del_b, (
            f"{theme_name} diff_del {tokens['diff_del']} reads as red"
        )

    @pytest.mark.parametrize("theme_name", THEME_NAMES)
    def test_provider_brand_color_overrides_logo_in_every_theme(self, theme_name):
        """The connected-provider identity survives every palette swap."""
        apply_theme(theme_name)
        from nexus.tui.widgets import resolve_welcome_identity

        color, badge = resolve_welcome_identity("groq", "llama-3.3-70b")
        assert color == "#f55036"  # groq brand, NOT the theme logo_tint
        assert badge == "⬢ groq · llama-3.3-70b"

    @pytest.mark.parametrize("theme_name", THEME_NAMES)
    def test_no_provider_logo_follows_theme_tint(self, theme_name):
        apply_theme(theme_name)
        from nexus.tui.widgets import resolve_welcome_identity

        color, badge = resolve_welcome_identity("", "")
        assert color == THEMES[theme_name]["logo_tint"]
        assert badge is None


class TestThemeApplication:
    def test_apply_theme_swaps_tokens_in_place(self):
        """Widgets import CSS_COLORS by reference — the swap must mutate the
        SAME dict object, never rebind it."""
        before_id = id(CSS_COLORS)
        before_accent = CSS_COLORS["accent"]
        apply_theme("synthwave")
        assert id(CSS_COLORS) == before_id
        assert CSS_COLORS["accent"] == THEMES["synthwave"]["accent"]
        assert CSS_COLORS["accent"] != before_accent
        assert get_current_theme() == "synthwave"

    def test_apply_theme_restores_default_exactly(self):
        snapshot = dict(THEMES["neural-cyan"])
        apply_theme("mono")
        apply_theme("neural-cyan")
        assert CSS_COLORS == snapshot

    def test_unknown_theme_keeps_current(self):
        apply_theme("vaporwave-9000")
        assert get_current_theme() == DEFAULT_THEME

    def test_partial_theme_rejected(self):
        broken = {"accent": "#ffffff"}  # missing every other token
        THEMES["_broken_test"] = broken
        try:
            with pytest.raises(ValueError):
                apply_theme("_broken_test")
            assert get_current_theme() == DEFAULT_THEME
        finally:
            THEMES.pop("_broken_test", None)

    def test_cycle_theme_wraps(self):
        apply_theme(THEME_NAMES[0])
        assert cycle_theme() == THEME_NAMES[1]
        # Cycle len(THEME_NAMES)-1 more times to land back on the first.
        for _ in range(len(THEME_NAMES) - 1):
            cycle_theme()
        assert get_current_theme() == THEME_NAMES[0]

    def test_theme_token_accessor(self):
        apply_theme("matrix")
        assert theme_token("accent") == THEMES["matrix"]["accent"]

    def test_theme_listeners_fire_and_can_fail_safely(self):
        seen = []
        from nexus.tui.colors import on_theme_change

        def boom(_name):
            raise RuntimeError("listener exploded")

        un_sub = on_theme_change(lambda name: seen.append(name))
        un_boom = on_theme_change(boom)
        try:
            apply_theme("amber-crt")
            assert "amber-crt" in seen  # the healthy listener still ran
        finally:
            un_sub()
            un_boom()
        apply_theme(DEFAULT_THEME)


class TestThemeLiveWidgetRefresh:
    @pytest.mark.asyncio
    async def test_status_bar_render_follows_theme(self):
        """A live swap re-colors widget renders with zero restarts."""
        from textual.app import App

        from nexus.tui.widgets import StatusBar

        class HarnessApp(App):
            def compose(self):
                yield StatusBar(version="0.1.0", model="m", provider="")

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.query_one(StatusBar)
            # Busy state → the pulse dot renders with the live accent token,
            # which is what changes between themes.
            from nexus.tui.state import get_state_manager

            get_state_manager().set_busy(True)
            apply_theme("neural-cyan")
            await pilot.pause()
            cyan_render = bar.render()
            assert "00f2ff" in cyan_render

            apply_theme("synthwave")
            await pilot.pause()
            synth_render = bar.render()
            assert "ff3ec8" in synth_render
            assert synth_render != cyan_render

    @pytest.mark.asyncio
    async def test_welcome_card_retints_on_theme_swap(self):
        from textual.app import App

        from nexus.tui.widgets import WelcomeCard

        card = WelcomeCard(version="9.9.9")

        class HarnessApp(App):
            def compose(self):
                yield card

        app = HarnessApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            apply_theme("mono")
            card.refresh_theme()
            await pilot.pause()
            # Markup is parsed at update time, so the tint lives in spans.
            content = card.query_one(".welcome-logo").render()
            styles = [str(sp.style) for sp in content.spans]
            assert any(THEMES["mono"]["logo_tint"] in s for s in styles)

    @pytest.mark.asyncio
    async def test_app_boot_applies_settings_theme(self, monkeypatch):
        """NexusTUI boots into whatever theme the persisted settings hold."""
        from nexus.tui.settings import get_settings, reset_settings_instance

        monkeypatch.setenv("NEXUS_TUI_SETTINGS_PATH", "/nonexistent/settings.json")
        reset_settings_instance()
        get_settings().theme = "matrix"

        from nexus.tui.app import NexusTUI

        app = NexusTUI()
        try:
            async with app.run_test(size=(90, 30)) as pilot:
                await pilot.pause()
                assert get_current_theme() == "matrix"
        finally:
            apply_theme(DEFAULT_THEME)
            get_settings().theme = DEFAULT_THEME
            reset_settings_instance()

    @pytest.mark.asyncio
    async def test_tool_status_icons_recolor_on_swap(self):
        from nexus.tui.widgets import TOOL_STATUS_ICONS

        apply_theme("neural-cyan")
        assert TOOL_STATUS_ICONS["done"][1] == THEMES["neural-cyan"]["success"]
        apply_theme("synthwave")
        assert TOOL_STATUS_ICONS["done"][1] == THEMES["synthwave"]["success"]
