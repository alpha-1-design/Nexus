"""Onboarding wizard — a polished, interactive setup experience for Nexus.

Transforms provider configuration from a chore into a guided journey
with rich terminal UI, live connection testing, smart defaults,
and gentle error recovery. Covers providers, models, tools, plugins,
search, and personalisation.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from ..config import NexusConfig, ProviderConfig, save_config


# =============================================================================
# Terminal styling utilities
# =============================================================================


def _c(text: str, fg: str | None = None, bold: bool = False, **kw: Any) -> str:
    kw["fg"] = fg or kw.get("fg")
    if bold:
        kw["bold"] = True
    return click.style(text, **kw)


def _rule(char: str = "─", length: int | None = None) -> str:
    cols = length or (os.get_terminal_size().columns - 2)
    return _c(char * cols, fg="bright_black")


def _box(title: str, width: int | None = None) -> None:
    """Print a bordered section header."""
    cols = width or (os.get_terminal_size().columns - 2)
    top = _c(f"┌{'─' * (cols - 2)}┐", fg="bright_black")
    mid = (
        _c("│", fg="bright_black")
        + _c(f" {title}".ljust(cols - 2), bold=True)
        + _c("│", fg="bright_black")
    )
    bot = _c(f"└{'─' * (cols - 2)}┘", fg="bright_black")
    click.echo(f"  {top}")
    click.echo(f"  {mid}")
    click.echo(f"  {bot}")


def _bullet(text: str, color: str = "white", bold: bool = False) -> None:
    click.echo(f"  {_c('•', 'bright_black')} {_c(text, fg=color, bold=bold)}")


def _step(n: int, total: int, label: str) -> None:
    """Print a step header like 'Step 3/10 — API Key'."""
    click.echo()
    click.echo(
        f"  {_c(f'Step {n}/{total}', 'cyan')}  {_c(f'─ {label}', 'white', bold=True)}"
    )
    click.echo(f"  {_rule('·', 50)}")


def _ok(message: str) -> None:
    click.echo(f"  {_c('✔', 'green')} {message}")


def _warn(message: str) -> None:
    click.echo(f"  {_c('⚠', 'yellow')} {message}")


def _err(message: str) -> None:
    click.echo(f"  {_c('✘', 'red')} {message}")


# =============================================================================
# Multi-select helper
# =============================================================================


def _multi_select(
    prompt: str,
    options: list[tuple[str, str, str]],  # (id, label, description)
    defaults: list[str] | None = None,
    max_cols: int = 60,
) -> list[str]:
    """Interactive multi-select with toggles."""
    selected = set(defaults or [])
    click.echo()

    def _render():
        for idx, (opt_id, label, desc) in enumerate(options, 1):
            mark = _c("●", "green") if opt_id in selected else _c("○", "bright_black")
            click.echo(
                f"    {_c(f'{idx:2d}.', 'bright_black')} {mark}  {_c(label, 'white', bold=True)}"
            )
            click.echo(f"          {_c(desc, 'bright_black')}")

    _render()
    click.echo()
    click.echo(
        f"  {_c('Enter numbers to toggle (comma-separated, or 0 for none)', 'bright_black')}"
    )
    click.echo(f"  {_c('Press Enter when done', 'bright_black')}")

    while True:
        raw = click.prompt(
            _c(f"  {prompt}", fg="cyan", bold=True),
            default="",
            show_default=False,
            prompt_suffix="",
        )
        if not raw.strip():
            break
        for part in raw.split(","):
            part = part.strip()
            if part == "0":
                selected.clear()
            else:
                try:
                    idx = int(part) - 1
                    if 0 <= idx < len(options):
                        opt_id = options[idx][0]
                        if opt_id in selected:
                            selected.discard(opt_id)
                        else:
                            selected.add(opt_id)
                except (ValueError, IndexError):
                    pass
        click.echo()
        _render()
        click.echo()

    return list(selected)


# =============================================================================
# Provider catalogue
# =============================================================================


@dataclass
class ProviderInfo:
    id: str
    name: str
    tier: str  # Free / Budget / Premium / Elite / Local
    speed: str
    quality: str
    description: str
    setup_url: str = ""
    default_model: str = ""
    env_vars: list[str] = field(default_factory=list)
    badge_color: str = "white"


PROVIDER_CATALOGUE: list[ProviderInfo] = [
    ProviderInfo(
        id="opencode-zen",
        name="OpenCode Zen",
        tier="Free",
        speed="Fast",
        quality="★★★★☆",
        description="Zero-cost, no API key required for basic use.",
        setup_url="https://opencode.ai/zen",
        default_model="minimax-m2.5-free",
        badge_color="green",
    ),
    ProviderInfo(
        id="groq",
        name="Groq",
        tier="Free",
        speed="Instant",
        quality="★★★★☆",
        description="Near-instant Llama inference. Generous free tier.",
        setup_url="https://console.groq.com/keys",
        default_model="llama-3.3-70b-versatile",
        env_vars=["GROQ_API_KEY"],
        badge_color="green",
    ),
    ProviderInfo(
        id="openai",
        name="OpenAI",
        tier="Premium",
        speed="Fast",
        quality="★★★★★",
        description="GPT-4o — industry standard, widely supported.",
        setup_url="https://platform.openai.com/api-keys",
        default_model="gpt-4o",
        env_vars=["OPENAI_API_KEY"],
        badge_color="blue",
    ),
    ProviderInfo(
        id="anthropic",
        name="Anthropic",
        tier="Premium",
        speed="Moderate",
        quality="★★★★★",
        description="Claude Opus / Sonnet — exceptional reasoning.",
        setup_url="https://console.anthropic.com/settings/keys",
        default_model="claude-sonnet-4-20250514",
        env_vars=["ANTHROPIC_API_KEY"],
        badge_color="blue",
    ),
    ProviderInfo(
        id="google",
        name="Google Gemini",
        tier="Free",
        speed="Fast",
        quality="★★★★☆",
        description="Gemini 2.0 Flash / Pro. Strong free tier.",
        setup_url="https://aistudio.google.com/app/apikey",
        default_model="gemini-2.0-flash",
        env_vars=["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        badge_color="green",
    ),
    ProviderInfo(
        id="openrouter",
        name="OpenRouter",
        tier="Budget",
        speed="Moderate",
        quality="★★★★☆",
        description="200+ models. Pay-per-token, many free options.",
        setup_url="https://openrouter.ai/keys",
        default_model="google/gemma-3-27b-it:free",
        env_vars=["OPENROUTER_API_KEY"],
        badge_color="yellow",
    ),
    ProviderInfo(
        id="deepseek",
        name="DeepSeek",
        tier="Budget",
        speed="Fast",
        quality="★★★★☆",
        description="DeepSeek-V3 / R1 — excellent coding at low cost.",
        setup_url="https://platform.deepseek.com/api_keys",
        default_model="deepseek-chat",
        env_vars=["DEEPSEEK_API_KEY"],
        badge_color="yellow",
    ),
    ProviderInfo(
        id="together",
        name="Together AI",
        tier="Budget",
        speed="Fast",
        quality="★★★☆☆",
        description="Hosted open-source models. Good selection.",
        setup_url="https://api.together.xyz/settings/api-keys",
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        env_vars=["TOGETHER_API_KEY"],
        badge_color="yellow",
    ),
    ProviderInfo(
        id="mistral",
        name="Mistral AI",
        tier="Premium",
        speed="Fast",
        quality="★★★★☆",
        description="Mistral Large / Small — European, efficient.",
        setup_url="https://console.mistral.ai/api-keys/",
        default_model="mistral-large-latest",
        env_vars=["MISTRAL_API_KEY"],
        badge_color="blue",
    ),
    ProviderInfo(
        id="ollama",
        name="Ollama (Local)",
        tier="Local",
        speed="Deliberate",
        quality="★★★☆☆",
        description="Run models on your machine. Private, offline, free.",
        setup_url="https://ollama.ai",
        default_model="qwen2.5-coder:7b",
        badge_color="magenta",
    ),
]


def _get_provider(provider_id: str) -> ProviderInfo | None:
    for p in PROVIDER_CATALOGUE:
        if p.id == provider_id:
            return p
    return None


_TIER_COLORS = {
    "Free": "green",
    "Budget": "yellow",
    "Premium": "blue",
    "Elite": "cyan",
    "Local": "magenta",
}


# =============================================================================
# Tool profiles
# =============================================================================

TOOL_PROFILES: list[tuple[str, str, str]] = [
    ("coding", "Coding Agent", "Full toolkit: read, write, edit, bash, search, git, web. Best for software engineering."),
    ("default", "Default", "Balanced set: read, write, edit, bash, search. Good general purpose."),
    ("minimal", "Minimal", "Read-only + bash. Safe for exploration and review."),
    ("all", "Everything", "All tools including automation, browser, clipboard, API. Maximum power."),
]

# =============================================================================
# The wizard
# =============================================================================

TOTAL_STEPS = 10


class OnboardingManager:
    """Interactive setup wizard — 10 steps + summary.

    1/10  Welcome & system
    2/10  Your name (optional)
    3/10  Provider
    4/10  API key
    5/10  Model
    6/10  Connection test
    7/10  Tool profile
    8/10  Search provider
    9/10  Plugins & skills
    10/10 Settings & fine-tune
    """

    def __init__(self, config: NexusConfig, non_interactive: bool = False):
        self.config = config
        self.non_interactive = non_interactive

        # Provider selections
        self.provider_id: str | None = None
        self.provider_info: ProviderInfo | None = None
        self.api_key: str = ""
        self.model: str = ""
        self.temperature: float = 0.7
        self.max_tokens: int = 4096
        self.base_url: str | None = None

        # Personalisation
        self.user_name: str = ""

        # Tool profile
        self.tool_profile: str = "coding"

        # Search provider
        self.search_provider: str = "exa"
        self.search_api_key: str = ""

        # Plugins & skills
        self.enable_plugins: bool = True
        self.enable_skills: bool = True

        # State
        self._key_source: str = ""

    # ============================================================
    # Public entry point
    # ============================================================

    def run(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> bool:
        """Run the full wizard. Returns True if setup completed."""
        if self.non_interactive:
            return self._run_non_interactive(provider, model, api_key)

        self._print_banner()
        self._print_system_info()

        self._step_name()
        self._step_provider()
        self._step_api_key()
        self._step_model()
        self._step_test()
        self._step_tool_profile()
        self._step_search()
        self._step_plugins()
        self._step_settings()

        if not self._step_summary():
            _warn("Setup cancelled.")
            return False

        self._finalize()
        self._print_outro()
        return True

    # ============================================================
    # Non-interactive mode
    # ============================================================

    def _run_non_interactive(
        self, provider: str | None, model: str | None, api_key: str | None
    ) -> bool:
        provider = provider or os.environ.get("NEXUS_PROVIDER") or ""
        info = _get_provider(provider)

        if not info:
            click.echo(f"  {_c('✘', 'red')} Unknown provider: {provider}")
            click.echo(
                f"  Supported: {', '.join(p.id for p in PROVIDER_CATALOGUE)}"
            )
            return False

        self.provider_id = provider
        self.provider_info = info
        self.model = model or info.default_model
        self.api_key = api_key or self._detect_env_key(info) or ""
        self.base_url = _get_base_url(provider)

        if not self.api_key and provider != "ollama":
            _warn(
                f"No API key found for {provider} (checked env: {info.env_vars})"
            )
            return False

        self._finalize()
        click.echo(f"  {_c('✔', 'green')} {provider} configured non-interactively")
        return True

    # ============================================================
    # Banner
    # ============================================================

    def _print_banner(self) -> None:
        cols = os.get_terminal_size().columns - 2

        logo_lines = [
            _c("  ╔═══╗╔╗ ╔╗╔═══╗╔═══╗╔═══╗╔═══╗", fg="cyan", bold=True),
            _c("  ╚══╗║║║ ║║║╔══╝║╔══╝║╔═╗║╚══╗║", fg="cyan"),
            _c("  ╔══╝║║╚═╝║║╚══╗║╚══╗║╚═╝║╔══╝║", fg="blue"),
            _c("  ╚═══╝╚═╝╚═╝╚═══╝╚═══╝╚═══╝╚═══╝", fg="blue", bold=True),
        ]

        click.echo()
        for logo in logo_lines:
            click.echo(f"  {logo}")
        click.echo()
        click.echo(
            f"  {_c('Neural OS  —  Setup Wizard', 'cyan', bold=True).center(cols)}"
        )
        click.echo(f"  {_rule()}")
        click.echo()

    def _print_system_info(self) -> None:
        info = [
            ("Platform", f"{platform.system()} {platform.release()}"),
            ("Python", platform.python_version()),
            ("Terminal", os.environ.get("TERM", "unknown")),
        ]
        if os.environ.get("TERMUX_VERSION"):
            info.append(("Termux", "detected"))

        click.echo(f"  {_c('SYSTEM', fg='bright_black', bold=True)}")
        for label, val in info:
            click.echo(
                f"    {_c(label + ':', fg='bright_black')} {_c(val, fg='white')}"
            )
        click.echo()

    # ============================================================
    # Step 2/10 — User name
    # ============================================================

    def _step_name(self) -> None:
        _step(2, TOTAL_STEPS, "What should I call you?")

        click.echo()
        click.echo(
            f"  {_c('Nexus can address you by name throughout our session.', 'bright_black')}"
        )
        click.echo(
            f"  {_c('This is purely optional — skip it if you prefer.', 'bright_black')}"
        )
        click.echo()

        name = click.prompt(
            _c("  Your name", fg="cyan", bold=True),
            default="",
            show_default=False,
            prompt_suffix="",
        )
        if name.strip():
            self.user_name = name.strip()
            _ok(f"Nice to meet you, {_c(self.user_name, bold=True)}!")
        else:
            self.user_name = ""
            _ok("No name set — I'll keep it professional.")

    # ============================================================
    # Step 3/10 — Provider
    # ============================================================

    def _step_provider(self) -> None:
        _step(3, TOTAL_STEPS, "Choose your AI provider")

        click.echo()
        click.echo(f"  {_c('Available providers:', 'white', bold=True)}")

        tiers = ["Free", "Budget", "Premium", "Local"]
        for tier in tiers:
            providers_in_tier = [p for p in PROVIDER_CATALOGUE if p.tier == tier]
            if not providers_in_tier:
                continue
            color = _TIER_COLORS.get(tier, "white")
            click.echo()
            click.echo(f"    {_c(f'▸ {tier.upper()}', fg=color, bold=True)}")

            for idx, p in enumerate(providers_in_tier, 1):
                num = _c(f"  {idx}.", "bright_black")
                name = _c(f"{p.name:22s}", fg="white", bold=True)
                rating = _c(p.quality, fg="yellow")
                speed = _c(p.speed, fg="bright_black")
                click.echo(f"      {num} {name} {rating}  {speed}")
                click.echo(f"           {_c(p.description, fg='bright_black')}")

        click.echo()
        click.echo(
            f"  {_c('Enter the number of your choice', fg='bright_black')}  "
            f"{_c('[default: 1 = OpenCode Zen]', fg='bright_black')}"
        )

        choice = click.prompt(
            _c("  Provider", fg="cyan", bold=True),
            type=click.IntRange(1, len(PROVIDER_CATALOGUE)),
            default=1,
            show_default=False,
            prompt_suffix="",
        )
        self.provider_info = PROVIDER_CATALOGUE[choice - 1]
        self.provider_id = self.provider_info.id
        self.model = self.provider_info.default_model
        self.base_url = _get_base_url(self.provider_id)

        _ok(f"Selected {_c(self.provider_info.name, bold=True)}")

    # ============================================================
    # Step 4/10 — API key
    # ============================================================

    def _step_api_key(self) -> None:
        _step(4, TOTAL_STEPS, "API Key")

        info = self.provider_info
        if not info:
            return

        if info.id == "ollama":
            _ok("Ollama runs locally — no API key needed")
            self.api_key = ""
            self._key_source = "none"
            return

        env_key = self._detect_env_key(info)
        if env_key:
            masked = (
                env_key[:8] + "…" + env_key[-4:]
                if len(env_key) > 12
                else "********"
            )
            self.api_key = env_key
            self._key_source = "env"
            _ok(f"Key found in environment: {_c(masked, 'bright_black')}")
            return

        click.echo()
        click.echo(
            f"  {_c('Get your key at:', 'bright_black')}  {_c(info.setup_url, 'blue', bold=True)}"
        )
        click.echo()

        while True:
            key = click.prompt(
                _c("  API Key", fg="cyan", bold=True),
                hide_input=True,
                default="",
                show_default=False,
                prompt_suffix="",
            )
            if key.strip():
                self.api_key = key.strip()
                self._key_source = "prompt"
                _ok("Key recorded")
                break
            click.echo(
                f"  {_c('Key cannot be empty. Press Ctrl+C to cancel.', 'yellow')}"
            )

    def _detect_env_key(self, info: ProviderInfo) -> str:
        for var in info.env_vars:
            val = os.environ.get(var, "")
            if val:
                return val
        return ""

    # ============================================================
    # Step 5/10 — Model (live from API)
    # ============================================================

    def _step_model(self) -> None:
        _step(5, TOTAL_STEPS, "Model")

        info = self.provider_info
        if not info:
            return

        # Fetch models live from the provider API
        live_models = self._fetch_live_models(info)
        if live_models:
            models = live_models
        else:
            # fallback to curated list if API unreachable
            models = _get_models_for_provider(info.id)

        click.echo()
        click.echo(
            f"  {_c('Available models for', 'white', bold=True)} {_c(info.name, fg='cyan', bold=True)}"
        )
        click.echo(f"  {_rule('·', 50)}")

        for idx, m in enumerate(models, 1):
            name = _c(f"{m['name']:35s}", bold=True)
            desc = _c(m.get("description", ""), fg="bright_black")
            click.echo(f"    {_c(str(idx) + '.', 'bright_black')} {name} {desc}")
            if m.get("warning"):
                click.echo(f"         {_c('⚠ ' + m['warning'], 'yellow')}")
            if m.get("badge"):
                badge_color = "green" if "fast" in m["badge"].lower() else "cyan"
                click.echo(f"         {_c(m['badge'], fg=badge_color)}")

        click.echo()
        click.echo(
            f"  {_c('Enter model number [default: 1]', 'bright_black')}"
        )

        choice = click.prompt(
            _c("  Model", fg="cyan", bold=True),
            type=click.IntRange(1, len(models)),
            default=1,
            show_default=False,
            prompt_suffix="",
        )
        self.model = models[choice - 1]["name"]
        _ok(f"Selected {_c(self.model, bold=True)}")

    def _fetch_live_models(self, info: ProviderInfo) -> list[dict[str, str]]:
        """Fetch models live from the provider API.

        Returns a list of {name, description} dicts, or empty list on failure.
        """
        import asyncio

        from nexus.models import get_registry

        if info.id == "ollama":
            return []  # let fallback handle it

        registry = get_registry()
        try:
            raw = asyncio.run(
                registry.fetch_models(
                    provider=info.id,
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            )
            if raw:
                return [{"name": m.id, "description": "", "badge": ""} for m in raw]
        except Exception:
            pass
        return []

    # ============================================================
    # Step 6/10 — Connection test + auto-discover
    # ============================================================

    def _step_test(self) -> None:
        _step(6, TOTAL_STEPS, "Connection test")

        info = self.provider_info
        if not info or info.id == "ollama":
            _ok("Skipped (local provider)")
            return

        click.echo()
        click.echo(
            f"  {_c('Testing connection to', 'bright_black')} {_c(info.name, bold=True)}"
        )
        click.echo(f"  {_c('Model:', 'bright_black')} {self.model}")

        spinners = ["◢", "◣", "◤", "◥"]
        for _ in range(20):
            for s in spinners:
                click.echo(f"\r  {_c(s, 'cyan')} Connecting…", nl=False)
                time.sleep(0.05)

        click.echo()

        result = _test_connection(
            provider_id=self.provider_id or "",
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
        )

        if result.get("ok"):
            _ok("Connection successful!")
            return

        _warn(f"Connection failed: {result.get('error', 'unknown error')}")
        click.echo()

        # Offer to auto-discover working models
        if click.confirm(
            _c("  Scan all models from this provider to find accessible ones?", fg="cyan", bold=True),
            default=True,
        ):
            discovered = self._discover_working_models(info)
            if discovered:
                click.echo()
                _ok(f"Switched to {_c(discovered, bold=True)}")
                self.model = discovered
                return

        # Fallback: skip or abort
        click.echo(
            f"  {_c('You can skip and fix this later.', 'bright_black')}"
        )
        click.echo(
            f"  {_c('Run', 'bright_black')} {_c('nexus setup', 'cyan')} {_c('to reconfigure.', 'bright_black')}"
        )
        if not click.confirm(
            _c("  Continue anyway?", fg="yellow", bold=True), default=True
        ):
            _err("Setup aborted")
            sys.exit(1)

    def _discover_working_models(self, info: ProviderInfo) -> str | None:
        """Scan all models for this provider and let user pick one.

        Returns the selected model id, or None.
        """
        import asyncio

        from nexus.models import get_registry

        click.echo()
        click.echo(
            f"  {_c('Scanning models from', 'cyan')} {_c(info.name, bold=True)} API ..."
        )

        registry = get_registry()
        spinner_frames = ["◢", "◣", "◤", "◥"]

        try:
            models = asyncio.run(
                registry.fetch_models(
                    provider=info.id,
                    api_key=self.api_key,
                    base_url=self.base_url,
                )
            )
        except Exception as e:
            _warn(f"Failed to fetch models: {e}")
            return None

        if not models:
            _warn("No models returned by API")
            return None

        # Ping them all in parallel
        click.echo()

        from nexus.models.pinger import ModelPinger

        pinger = ModelPinger(concurrency=15)
        try:
            report = asyncio.run(
                pinger.ping_provider(
                    provider=info.id,
                    api_key=self.api_key,
                    models=[m.id for m in models],
                    base_url=self.base_url,
                    timeout=5.0,
                )
            )
        except Exception as e:
            _warn(f"Ping failed: {e}")
            return None

        working = report.working
        if not working:
            _warn("No accessible models found")
            return None

        # Let user pick
        click.echo()
        click.echo(f"  {_c('Accessible models:', 'green', bold=True)}")
        click.echo(f"  {_rule('·', 50)}")

        sorted_working = sorted(working, key=lambda r: r.latency_ms)
        for idx, r in enumerate(sorted_working, 1):
            click.echo(
                f"    {_c(f'{idx:2d}.', 'bright_black')}  "
                f"{_c('✔', 'green')}  {_c(r.model_id, bold=True):40s}  "
                f"{_c(f'{r.latency_ms:.0f}ms', 'bright_black')}"
            )

        click.echo()
        choice = click.prompt(
            _c("  Choose a model", fg="cyan", bold=True),
            type=click.IntRange(1, len(sorted_working)),
            default=1,
            show_default=False,
            prompt_suffix="",
        )
        return sorted_working[choice - 1].model_id

    # ============================================================
    # Step 7/10 — Tool profile
    # ============================================================

    def _step_tool_profile(self) -> None:
        _step(7, TOTAL_STEPS, "Toolkit")

        click.echo()
        click.echo(
            f"  {_c('Choose the toolset that matches your workflow.', 'bright_black')}"
        )
        click.echo()

        for idx, (prof_id, label, desc) in enumerate(TOOL_PROFILES, 1):
            mark = _c("●", "green") if prof_id == self.tool_profile else _c("○", "bright_black")
            click.echo(
                f"    {_c(f'{idx:2d}.', 'bright_black')} {mark}  {_c(label, 'white', bold=True)}"
            )
            warning = ""
            if prof_id == "all":
                warning = _c(" ⚠ Requires permission for each action", "yellow")
            click.echo(f"          {_c(desc, 'bright_black')}{warning}")

        click.echo()
        choice = click.prompt(
            _c("  Profile", fg="cyan", bold=True),
            type=click.IntRange(1, len(TOOL_PROFILES)),
            default=1,
            show_default=False,
            prompt_suffix="",
        )
        self.tool_profile = TOOL_PROFILES[choice - 1][0]
        _ok(f"Toolkit: {_c(TOOL_PROFILES[choice - 1][1], bold=True)}")

    # ============================================================
    # Step 8/10 — Search provider
    # ============================================================

    def _step_search(self) -> None:
        _step(8, TOTAL_STEPS, "Web search")

        click.echo()

        search_options = [
            ("exa", "Exa", "Best for code & technical search. API key optional for basic use."),
            ("tavily", "Tavily", "Optimised for AI agents. Requires API key."),
            ("brave", "Brave Search", "Privacy-first. Requires API key."),
        ]

        click.echo(f"  {_c('Search engine for web lookups:', 'white', bold=True)}")
        click.echo()

        for idx, (sid, label, desc) in enumerate(search_options, 1):
            mark = _c("●", "green") if sid == self.search_provider else _c("○", "bright_black")
            click.echo(
                f"    {_c(f'{idx:2d}.', 'bright_black')} {mark}  {_c(label, 'white', bold=True)}"
            )
            click.echo(f"          {_c(desc, 'bright_black')}")

        click.echo()
        choice = click.prompt(
            _c("  Search provider", fg="cyan", bold=True),
            type=click.IntRange(1, len(search_options)),
            default=1,
            show_default=False,
            prompt_suffix="",
        )
        self.search_provider = search_options[choice - 1][0]

        # API key for search (optional for Exa)
        key_name_map = {"exa": "Exa", "tavily": "Tavily", "brave": "Brave"}
        key_env_map = {
            "exa": "EXA_API_KEY",
            "tavily": "TAVILY_API_KEY",
            "brave": "BRAVE_API_KEY",
        }

        env_key = os.environ.get(key_env_map[self.search_provider], "")
        if env_key:
            self.search_api_key = env_key
            _ok(f"API key found in environment ({key_env_map[self.search_provider]})")
        elif self.search_provider != "exa":
            click.echo()
            key = click.prompt(
                _c(f"  {key_name_map[self.search_provider]} API key", fg="cyan", bold=True),
                hide_input=True,
                default="",
                show_default=False,
                prompt_suffix="",
            )
            if key.strip():
                self.search_api_key = key.strip()
                _ok("Key recorded")
        else:
            _ok("Exa works without a key for basic searches")

    # ============================================================
    # Step 9/10 — Plugins & skills
    # ============================================================

    def _step_plugins(self) -> None:
        _step(9, TOTAL_STEPS, "Extensions")

        click.echo()
        click.echo(
            f"  {_c('Plugins extend Nexus with custom behaviour (middleware).', 'bright_black')}"
        )
        click.echo(
            f"  {_c('Skills are Markdown files that teach Nexus about specific domains.', 'bright_black')}"
        )

        click.echo()
        self.enable_plugins = click.confirm(
            _c("  Enable plugin system?", fg="cyan", bold=True),
            default=True,
        )
        if self.enable_plugins:
            _ok("Plugins enabled  (drop .py files in ~/.nexus/plugins/)")

        click.echo()
        self.enable_skills = click.confirm(
            _c("  Enable skills system?", fg="cyan", bold=True),
            default=True,
        )
        if self.enable_skills:
            _ok("Skills enabled  (use  nexus skill search <query>  to find community skills)")

    # ============================================================
    # Step 10/10 — Settings & fine-tune
    # ============================================================

    def _step_settings(self) -> None:
        _step(10, TOTAL_STEPS, "Fine-tune")

        click.echo()
        click.echo(
            f"  {_c('These are optional. Press Enter to accept defaults.', 'bright_black')}"
        )
        click.echo()

        self.temperature = click.prompt(
            _c("  Temperature", fg="cyan", bold=True),
            type=click.FloatRange(0.0, 2.0),
            default=0.7,
            show_default=True,
            prompt_suffix="",
        )
        self.max_tokens = click.prompt(
            _c("  Max tokens", fg="cyan", bold=True),
            type=click.IntRange(256, 128000),
            default=4096,
            show_default=True,
            prompt_suffix="",
        )

        info = self.provider_info
        if info and info.id == "ollama":
            default_url = os.environ.get(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            )
            url = click.prompt(
                _c("  Ollama Base URL", fg="cyan", bold=True),
                default=default_url,
                show_default=True,
                prompt_suffix="",
            )
            if url:
                self.base_url = url

    # ============================================================
    # Summary
    # ============================================================

    def _step_summary(self) -> bool:
        info = self.provider_info
        if not info:
            return False

        _box("Review your configuration")
        click.echo()

        # Personalisation
        if self.user_name:
            click.echo(
                f"  {_c('Name', bold=True):20s} {_c(self.user_name, fg='white', bold=True)}"
            )

        # Provider
        click.echo(
            f"  {_c('Provider', bold=True):20s} {_c(info.name, fg='cyan', bold=True)}"
        )
        click.echo(f"  {_c('Model', bold=True):20s} {_c(self.model, fg='white')}")

        if self.api_key:
            masked = (
                self.api_key[:8] + "…" + self.api_key[-4:]
                if len(self.api_key) > 12
                else "********"
            )
            click.echo(
                f"  {_c('API Key', bold=True):20s} {_c(masked, fg='bright_black')}  {_c(f'({self._key_source})', fg='bright_black')}"
            )
        else:
            click.echo(
                f"  {_c('API Key', bold=True):20s} {_c('none (local)', fg='yellow')}"
            )

        # Tools
        profile_label = next(
            (lbl for pid, lbl, _ in TOOL_PROFILES if pid == self.tool_profile),
            self.tool_profile,
        )
        click.echo(
            f"  {_c('Toolkit', bold=True):20s} {_c(profile_label, fg='white')}"
        )

        # Search
        click.echo(
            f"  {_c('Search', bold=True):20s} {_c(self.search_provider, fg='white')}"
            + (
                _c("  (key set)", "green")
                if self.search_api_key
                else _c("  (no key)", "bright_black")
            )
        )

        # Extensions
        plug = _c("on", "green") if self.enable_plugins else _c("off", "bright_black")
        skl = _c("on", "green") if self.enable_skills else _c("off", "bright_black")
        click.echo(f"  {_c('Plugins', bold=True):20s} {plug}")
        click.echo(f"  {_c('Skills', bold=True):20s} {skl}")

        # Settings
        click.echo(f"  {_c('Temperature', bold=True):20s} {self.temperature}")
        click.echo(f"  {_c('Max tokens', bold=True):20s} {self.max_tokens}")
        if self.base_url:
            click.echo(
                f"  {_c('Base URL', bold=True):20s} {_c(self.base_url, fg='bright_black')}"
            )

        click.echo()
        click.echo(f"  {_rule('·', 50)}")
        click.echo(
            f"  {_c('Config location:', 'bright_black')}  {_c(str(Path.home() / '.nexus' / 'config.json'), fg='bright_black')}"
        )
        click.echo()

        return click.confirm(
            _c("  Apply this configuration?", fg="green", bold=True),
            default=True,
        )

    # ============================================================
    # Finalize
    # ============================================================

    def _finalize(self) -> None:
        info = self.provider_info
        if not info or not self.provider_id:
            return

        p_type = info.id
        if info.id in ("ollama", "opencode-zen"):
            p_type = "openai"

        p_cfg = ProviderConfig(
            name=self.provider_id,
            provider_type=p_type,
            api_key=self.api_key,
            model=self.model,
            base_url=self.base_url or "",
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )

        self.config.providers = {self.provider_id: p_cfg}
        self.config.active_provider = self.provider_id
        self.config.first_run = False
        self.config.tool_profile = self.tool_profile
        self.config.search_provider = self.search_provider

        # Store user name if set
        if self.user_name:
            self.config.user_name = self.user_name

        # Search API keys
        key_map = {
            "exa": ("exa_api_key", self.search_api_key),
            "tavily": ("tavily_api_key", self.search_api_key),
            "brave": ("brave_api_key", self.search_api_key),
        }
        attr, val = key_map.get(self.search_provider, ("", ""))
        if attr and val:
            setattr(self.config, attr, val)

        save_config(self.config)

        # Ensure plugin/skill dirs exist
        if self.enable_plugins:
            Path(self.config.plugins_dir).mkdir(parents=True, exist_ok=True)
        if self.enable_skills:
            Path(self.config.skills_dir).mkdir(parents=True, exist_ok=True)

    # ============================================================
    # Outro
    # ============================================================

    def _print_outro(self) -> None:
        info = self.provider_info
        if not info:
            return

        cols = os.get_terminal_size().columns - 2

        click.echo()
        click.echo(f"  {_c('═' * (cols - 2), 'green')}")
        click.echo(
            f"  {_c('✔  Setup complete!', 'green', bold=True).center(cols - 2)}"
        )
        click.echo(f"  {_c('═' * (cols - 2), 'green')}")
        click.echo()

        greeting = f"Nexus is ready"
        if self.user_name:
            greeting = f"{self.user_name}, Nexus is ready"
        click.echo(f"  {_c(greeting, 'white', bold=True)}")
        click.echo()

        click.echo(f"  {_c('Next steps:', 'bright_black', bold=True)}")
        click.echo(
            f"    {_c('nexus repl', 'cyan', bold=True)}        {_c('Start an interactive session', 'bright_black')}"
        )
        click.echo(
            f"    {_c('nexus status', 'cyan', bold=True)}      {_c('Check your configuration', 'bright_black')}"
        )
        click.echo(
            f"    {_c('nexus doctor', 'cyan', bold=True)}      {_c('Run diagnostics', 'bright_black')}"
        )
        if self.api_key:
            click.echo(
                f"    {_c('nexus auth check', 'cyan', bold=True)}   {_c('Verify connectivity', 'bright_black')}"
            )
        click.echo(
            f"    {_c('nexus skill search', 'cyan', bold=True)}  {_c('Browse community skills', 'bright_black')}"
        )

        if self.user_name:
            click.echo()
            click.echo(
                f"  {_c('💡 Tip:', 'bright_black')}  You can always change your name later with "
                f"{_c('nexus config set user_name <name>', 'cyan')}"
            )

        click.echo()


# =============================================================================
# Helpers
# =============================================================================


def _get_base_url(provider_id: str) -> str | None:
    urls = {
        "opencode-zen": "https://opencode.ai/zen/v1",
        "opencode-go": "https://opencode.ai/zen/go/v1",
        "ollama": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        "google": "https://generativelanguage.googleapis.com/v1beta",
    }
    return urls.get(provider_id)


def _get_models_for_provider(provider_id: str) -> list[dict[str, str]]:
    catalogue: dict[str, list[dict[str, str]]] = {
        "openai": [
            {"name": "gpt-4o", "description": "Flagship — best overall", "badge": "⭐ Recommended"},
            {"name": "gpt-4o-mini", "description": "Fast & cheap", "badge": "💰 Budget-friendly"},
            {"name": "o3-mini", "description": "Reasoning — excels at logic & code"},
            {"name": "gpt-4.1-nano", "description": "Ultra-fast, ultra-cheap"},
        ],
        "anthropic": [
            {"name": "claude-sonnet-4-20250514", "description": "Best balance", "badge": "⭐ Recommended"},
            {"name": "claude-opus-4-20250514", "description": "Maximum reasoning"},
            {"name": "claude-haiku-3-5-20241022", "description": "Fastest, cheapest", "badge": "⚡ Speed"},
        ],
        "groq": [
            {"name": "llama-3.3-70b-versatile", "description": "Best overall", "badge": "⭐ Recommended"},
            {"name": "llama-3.1-8b-instant", "description": "Lightning fast", "badge": "⚡ Speed"},
            {"name": "mixtral-8x7b-32768", "description": "Mixture of experts"},
            {"name": "gemma2-9b-it", "description": "Google's efficient model"},
        ],
        "google": [
            {"name": "gemini-2.0-flash", "description": "Fast, capable, free", "badge": "⭐ Recommended"},
            {"name": "gemini-2.0-flash-lite", "description": "Even faster"},
            {"name": "gemini-2.5-pro-exp-03-25", "description": "Maximum power (exp)"},
        ],
        "openrouter": [
            {"name": "google/gemma-3-27b-it:free", "description": "Free, capable", "badge": "💰 Free"},
            {"name": "openai/gpt-4o-mini", "description": "Industry standard, cheap"},
            {"name": "anthropic/claude-3.5-sonnet", "description": "Top-tier coding"},
        ],
        "deepseek": [
            {"name": "deepseek-chat", "description": "DeepSeek-V3", "badge": "⭐ Recommended"},
            {"name": "deepseek-reasoner", "description": "DeepSeek-R1 reasoning"},
        ],
        "together": [
            {"name": "meta-llama/Llama-3.3-70B-Instruct-Turbo", "description": "Best overall"},
            {"name": "mistralai/Mixtral-8x22B-Instruct-v0.1", "description": "Large MoE model"},
        ],
        "mistral": [
            {"name": "mistral-large-latest", "description": "Flagship", "badge": "⭐ Recommended"},
            {"name": "mistral-small-latest", "description": "Fast & efficient"},
            {"name": "codestral-latest", "description": "Code-specialised"},
        ],
        "ollama": [
            {"name": "qwen2.5-coder:7b", "description": "Best coding 7b", "badge": "⭐ Recommended"},
            {"name": "qwen2.5-coder:14b", "description": "More capable (~12GB RAM)"},
            {"name": "llama3.1:8b", "description": "Well-rounded general model"},
            {"name": "codellama:7b", "description": "Code-specialised"},
            {"name": "deepseek-coder-v2", "description": "Strong coding MoE"},
            {"name": "mistral:7b", "description": "Fast, lightweight"},
        ],
        "opencode-zen": [
            {"name": "minimax-m2.5-free", "description": "Fast & free", "badge": "⭐ Recommended"},
            {"name": "kimi-k2.5", "description": "Alternative free model"},
        ],
    }
    return catalogue.get(
        provider_id, [{"name": "gpt-4o", "description": "Default model"}]
    )


def _test_connection(
    provider_id: str, model: str, api_key: str, base_url: str | None
) -> dict:
    """Quick connectivity test."""
    try:
        import httpx

        headers: dict[str, str] = {}
        url: str = ""
        payload: dict[str, Any] = {}
        params: dict[str, str] | None = None

        if provider_id in ("opencode-zen", "opencode-go"):
            base = base_url or "https://opencode.ai/zen/v1"
            url = f"{base}/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}" if api_key else ""
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "anthropic":
            url = "https://api.anthropic.com/v1/messages"
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "google":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            params = {"key": api_key}
            payload = {"contents": [{"parts": [{"text": "ping"}]}]}
        elif provider_id == "groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "openrouter":
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "deepseek":
            url = "https://api.deepseek.com/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "together":
            url = "https://api.together.xyz/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "mistral":
            url = "https://api.mistral.ai/v1/chat/completions"
            headers["Authorization"] = f"Bearer {api_key}"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 2}
        elif provider_id == "ollama":
            url = f"{base_url or 'http://localhost:11434'}/api/chat"
            payload = {"model": model, "messages": [{"role": "user", "content": "ping"}], "stream": False}
        else:
            return {"ok": False, "error": f"Unknown provider: {provider_id}"}

        with httpx.Client(timeout=8.0) as client:
            resp = client.post(url, json=payload, headers=headers, params=params)

        if resp.status_code == 200:
            return {"ok": True}
        if resp.status_code == 401:
            return {"ok": False, "error": "Invalid API key (401)"}
        if resp.status_code == 429:
            return {"ok": False, "error": "Rate limited (429)"}
        try:
            msg = resp.json().get("error", {}).get("message", resp.text[:120])
        except Exception:
            msg = resp.text[:120]
        return {"ok": False, "error": f"HTTP {resp.status_code}: {msg}"}

    except httpx.ConnectError:
        return {"ok": False, "error": "Connection refused"}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Request timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
