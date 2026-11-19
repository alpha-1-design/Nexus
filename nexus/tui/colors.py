"""Color scheme constants for the Nexus TUI.

This is the single source of truth for colors used in Rich markup inside
widgets; styles.css mirrors the same palette for layout chrome.

Theme layer: CSS_COLORS is the LIVE token map the widgets read on every
render. `apply_theme()` swaps its *contents in place* (never rebinds it) so
every module holding a `from .colors import CSS_COLORS` reference sees the
new theme immediately — no restart, no re-import.
"""

CSS_COLORS = {
    "bg": "#05070a",
    "bg_elevated": "#0a0e14",
    "bg_input": "#05070a",
    "text": "#e0e6ed",
    "text_dim": "#64748b",
    "text_bright": "#ffffff",
    "accent": "#00f2ff",
    "accent_secondary": "#7c3aed",
    "error": "#f87171",
    "warning": "#fbbf24",
    "success": "#34d399",
    "info": "#3b82f6",
    "border": "#1f2937",
    "border_focus": "#00f2ff",
    "user_message": "#00f2ff",
    "assistant_message": "#7c3aed",
    "system_message": "#64748b",
    "tool_call": "#3b82f6",
    "timestamp": "#475569",
    # Unified diff rendering
    "diff_add": "#34d399",
    "diff_del": "#f87171",
    "diff_meta": "#67e8f9",
    # Glow colors
    "cyan": "#00f2ff",
    "purple": "#7c3aed",
    "blue": "#3b82f6",
    "green": "#10b981",
    # Welcome logo base tint (provider brand color overrides when connected)
    "logo_tint": "#00f2ff",
}

# Token every theme MUST define — `apply_theme` refuses partial maps so a
# theme can never half-render (missing role colors → Rich raises at render).
REQUIRED_TOKENS = frozenset(CSS_COLORS)

# ── Built-in themes ──────────────────────────────────────────────────────────
# +diff/-diff keep semantic green/red in every theme, tinted to fit the mood.
# `logo_tint` is the neutral identity color; a connected provider's brand
# color overrides it (see PROVIDER_BRAND_COLORS) in ALL themes.

SYNTHWAVE_COLORS = {
    "bg": "#0d0518",
    "bg_elevated": "#1a0f2e",
    "bg_input": "#0d0518",
    "text": "#f2e9ff",
    "text_dim": "#8b7bb8",
    "text_bright": "#ffffff",
    "accent": "#ff3ec8",
    "accent_secondary": "#b26bff",
    "error": "#ff5c8a",
    "warning": "#ffb84d",
    "success": "#3ef0a0",
    "info": "#6e6eff",
    "border": "#33204d",
    "border_focus": "#ff3ec8",
    "user_message": "#ff3ec8",
    "assistant_message": "#b26bff",
    "system_message": "#8b7bb8",
    "tool_call": "#6e6eff",
    "timestamp": "#5f4b8b",
    "diff_add": "#3ef0a0",
    "diff_del": "#ff5c7a",
    "diff_meta": "#c79bff",
    "cyan": "#4dd8ff",
    "purple": "#b26bff",
    "blue": "#6e6eff",
    "green": "#3ef0a0",
    "logo_tint": "#ff3ec8",
}

MATRIX_COLORS = {
    "bg": "#000000",
    "bg_elevated": "#041008",
    "bg_input": "#000000",
    "text": "#a8ffa8",
    "text_dim": "#3f7f3f",
    "text_bright": "#eaffea",
    "accent": "#00ff41",
    "accent_secondary": "#39d353",
    "error": "#ff5f4f",
    "warning": "#ccb43a",
    "success": "#00ff41",
    "info": "#2fbf71",
    "border": "#0f2a0f",
    "border_focus": "#00ff41",
    "user_message": "#d0ffd0",
    "assistant_message": "#00ff41",
    "system_message": "#3f7f3f",
    "tool_call": "#39d353",
    "timestamp": "#2f662f",
    "diff_add": "#00ff41",
    "diff_del": "#ff5f4f",
    "diff_meta": "#66cc66",
    "cyan": "#39ff88",
    "purple": "#39d353",
    "blue": "#2fbf71",
    "green": "#00ff41",
    "logo_tint": "#00ff41",
}

AMBER_CRT_COLORS = {
    "bg": "#100a02",
    "bg_elevated": "#1a1204",
    "bg_input": "#100a02",
    "text": "#f0c674",
    "text_dim": "#7d6640",
    "text_bright": "#ffe9b8",
    "accent": "#ffb000",
    "accent_secondary": "#ff8c42",
    "error": "#ff5540",
    "warning": "#ffcf40",
    "success": "#8fbf4a",
    "info": "#d9a05b",
    "border": "#3a2c10",
    "border_focus": "#ffb000",
    "user_message": "#ffd24d",
    "assistant_message": "#ffa43b",
    "system_message": "#7d6640",
    "tool_call": "#d99a3d",
    "timestamp": "#6b5530",
    "diff_add": "#8fbf4a",
    "diff_del": "#ff5540",
    "diff_meta": "#e8b968",
    "cyan": "#ffd98a",
    "purple": "#c98a4b",
    "blue": "#d9a05b",
    "green": "#8fbf4a",
    "logo_tint": "#ffb000",
}

MONO_COLORS = {
    "bg": "#0a0a0a",
    "bg_elevated": "#141414",
    "bg_input": "#0a0a0a",
    "text": "#d4d4d4",
    "text_dim": "#6e6e6e",
    "text_bright": "#ffffff",
    "accent": "#e8e8e8",
    "accent_secondary": "#9a9a9a",
    "error": "#ae8f8f",
    "warning": "#a8a89a",
    "success": "#8fae8f",
    "info": "#8c8c8c",
    "border": "#262626",
    "border_focus": "#e8e8e8",
    "user_message": "#ffffff",
    "assistant_message": "#b8b8b8",
    "system_message": "#6e6e6e",
    "tool_call": "#9a9a9a",
    "timestamp": "#525252",
    # Diff stays semantically readable: heavily desaturated green/red that
    # still tint the right way on a grayscale palette.
    "diff_add": "#7ea87e",
    "diff_del": "#a87e7e",
    "diff_meta": "#909090",
    "cyan": "#cccccc",
    "purple": "#b0b0b0",
    "blue": "#a6a6a6",
    "green": "#8fae8f",
    "logo_tint": "#e8e8e8",
}

THEMES: dict[str, dict[str, str]] = {
    "neural-cyan": dict(CSS_COLORS),  # snapshot: the shipped default palette
    "synthwave": SYNTHWAVE_COLORS,
    "matrix": MATRIX_COLORS,
    "amber-crt": AMBER_CRT_COLORS,
    "mono": MONO_COLORS,
}

THEME_NAMES: tuple[str, ...] = tuple(THEMES)
DEFAULT_THEME = "neural-cyan"
_current_theme: str = DEFAULT_THEME
_theme_listeners: list = []


def get_current_theme() -> str:
    """Name of the active theme."""
    return _current_theme


def on_theme_change(callback) -> None:
    """Register a callback fired after every live theme swap (widgets use
    this to re-resolve their frozen per-role dicts). Returns a deregister fn."""
    _theme_listeners.append(callback)

    def _unsubscribe() -> None:
        try:
            _theme_listeners.remove(callback)
        except ValueError:
            pass

    return _unsubscribe


def apply_theme(name: str) -> str:
    """Swap the live token map to `name` IN PLACE and notify listeners.

    Returns the applied theme name; unknown names keep the current theme
    (callers decide whether to surface that). Partial/broken token maps are
    rejected so a theme can never half-render.
    """
    global _current_theme
    tokens = THEMES.get(name)
    if not tokens:
        return _current_theme
    missing = REQUIRED_TOKENS - set(tokens)
    if missing:
        raise ValueError(f"theme '{name}' is missing tokens: {sorted(missing)}")
    CSS_COLORS.clear()
    CSS_COLORS.update(tokens)
    _current_theme = name
    for callback in list(_theme_listeners):
        try:
            callback(name)
        except Exception:
            pass  # a broken listener must not break the swap
    return _current_theme


def theme_token(key: str) -> str:
    """Current-theme value for a token key (raises on unknown keys — a
    typo should fail loudly in tests, not render unstyled)."""
    return CSS_COLORS[key]


def cycle_theme(direction: int = 1) -> str:
    """Advance through THEME_NAMES (wraps) and apply; returns the new name."""
    try:
        idx = THEME_NAMES.index(_current_theme)
    except ValueError:
        idx = 0
    return apply_theme(THEME_NAMES[(idx + direction) % len(THEME_NAMES)])

# ── Provider brand colors ────────────────────────────────────────────────────
# The welcome card, status bar and REPL banner tint the NEXUS identity with
# the connected provider's brand color ("the identity follows the brain").
# Values are best-effort tints taken from each provider's public branding,
# chosen to read well on both dark Termux and desktop palettes; the neutral
# cyan accent is the fallback when no provider is configured or the name is
# unknown. Keys are canonical; config names like "github-models" resolve
# through PROVIDER_KEY_ALIASES.
PROVIDER_BRAND_COLORS: dict[str, str] = {
    "groq": "#f55036",
    "openrouter": "#8b5cf6",
    "cerebras": "#f97316",
    "gemini": "#4285f4",
    "mistral": "#ff7000",
    "github": "#58a6ff",
    "huggingface": "#ffd21e",
    "cloudflare": "#f48120",
    "zhipu": "#3859ff",
    "glm": "#3859ff",
    "ollama": "#d1d5db",
    "openai": "#10a37f",
    "anthropic": "#d97757",
    "deepseek": "#4d6bfe",
    "opencode": "#22d3ee",
}

# Config/config.py provider names (and common spellings) -> canonical brand key.
PROVIDER_KEY_ALIASES: dict[str, str] = {
    "google": "gemini",
    "github-models": "github",
    "cloudflare-workers-ai": "cloudflare",
    "zhipu": "glm",
    "zhipuai": "glm",
    "opencode-zen": "opencode",
    "opencode-go": "opencode",
}

# Neutral cyan when nothing matches — the classic Nexus look.
DEFAULT_BRAND_COLOR = CSS_COLORS["accent"]

_ANSI_RESET = "\033[0m"


def resolve_provider_key(provider_name: str | None) -> str | None:
    """Normalize a provider/config name to its canonical brand key, or None."""
    if not provider_name:
        return None
    key = str(provider_name).strip().lower()
    key = PROVIDER_KEY_ALIASES.get(key, key)
    return key if key in PROVIDER_BRAND_COLORS else None


def get_provider_brand_color(provider_name: str | None) -> str:
    """Brand hex for a provider name; neutral cyan fallback when unknown."""
    key = resolve_provider_key(provider_name)
    return PROVIDER_BRAND_COLORS.get(key, DEFAULT_BRAND_COLOR)


def format_provider_badge(provider_name: str, model: str = "") -> str:
    """Badge text like `⬢ groq · llama-3.3-70b` (no color — callers style it)."""
    badge = f"⬢ {provider_name}"
    if model:
        badge += f" · {model}"
    return badge


def hex_to_ansi24(hex_color: str) -> str:
    """Convert `#rrggbb` to a 24-bit foreground ANSI escape sequence."""
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return ""
    try:
        r, g, b = (int(value[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return ""
    return f"\033[38;2;{r};{g};{b}m"


ANSI_RESET = _ANSI_RESET
