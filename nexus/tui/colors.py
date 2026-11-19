"""Color scheme constants for the Nexus TUI.

This is the single source of truth for colors used in Rich markup inside
widgets; styles.css mirrors the same palette for layout chrome.
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
}

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
