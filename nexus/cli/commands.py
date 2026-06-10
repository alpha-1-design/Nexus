"""Nexus CLI - Command line interface for Nexus."""

import asyncio
import os
import sys
from pathlib import Path

import click

from nexus import __version__
from nexus.config import NexusConfig, save_config
from nexus.providers import Message, get_manager
from nexus.tools import get_registry
from nexus.voice import get_voice_engine, list_tts_voices

_style = click.style


@click.group()
@click.version_option(version=__version__)
@click.option("--config", type=click.Path(), help="Path to config file")
@click.pass_context
def cli(ctx: click.Context, config: str | None) -> None:
    """Nexus - Your AI Coding Agent.

    A powerful, self-hosted AI coding agent that combines the best features
    of OpenClaw, Claude Code, Gemini CLI, OpenCode, and NemoClaw.
    """
    from nexus.config import load_config

    ctx.ensure_object(dict)
    if config:
        ctx.obj["config"] = load_config(Path(config))
    else:
        ctx.obj["config"] = load_config()


@cli.command("upgrade")
def upgrade() -> None:
    """Upgrade Nexus to the latest version."""
    import subprocess

    click.echo(f"\n{chr(0x2728)} Checking for updates...")
    try:
        # Fetch latest
        subprocess.run(["git", "pull", "origin", "main"], check=True)
        # Re-install
        subprocess.run(["bash", "install.sh"], check=True)
        click.echo(f"\n{chr(0x2705)} Nexus upgraded successfully.")
    except Exception as e:
        click.echo(f"\n{chr(0x274C)} Upgrade failed: {e}")


# Provider commands
@cli.group()
def provider():
    """Manage AI providers."""
    pass


@provider.command("list")
@click.pass_context
def provider_list(ctx: click.Context) -> None:
    """List all configured providers."""
    from nexus.providers import get_manager

    config: NexusConfig = ctx.obj["config"]
    get_manager()

    click.echo("Configured providers:\n")
    for name, cfg in config.providers.items():
        status = "active" if name == config.active_provider else "inactive"
        click.echo(f"  {name} ({cfg.provider_type}) - {cfg.model} [{status}]")
        if cfg.base_url:
            click.echo(f"    URL: {cfg.base_url}")


@provider.command("add")
@click.argument("name")
@click.option(
    "--type",
    "provider_type",
    required=True,
    help="Provider type (openai, anthropic, google, ollama, groq, deepseek)",
)
@click.option("--api-key", help="API key")
@click.option("--base-url", help="Base URL (for custom endpoints)")
@click.option("--model", default="gpt-4o", help="Default model")
@click.pass_context
def provider_add(
    ctx: click.Context,
    name: str,
    provider_type: str,
    api_key: str | None,
    base_url: str | None,
    model: str,
) -> None:
    """Add a new AI provider."""
    from nexus.config import ProviderConfig, save_config

    config: NexusConfig = ctx.obj["config"]

    config.providers[name] = ProviderConfig(
        name=name,
        provider_type=provider_type,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )

    save_config(config)
    click.echo(f"Added provider: {name}")


@provider.command("remove")
@click.argument("name")
@click.pass_context
def provider_remove(ctx: click.Context, name: str) -> None:
    """Remove a provider."""
    from nexus.config import save_config

    config: NexusConfig = ctx.obj["config"]

    if name in config.providers:
        del config.providers[name]
        save_config(config)
        click.echo(f"Removed provider: {name}")
    else:
        click.echo(f"Provider not found: {name}", err=True)


@provider.command("set-active")
@click.argument("name")
@click.pass_context
def provider_set_active(ctx: click.Context, name: str) -> None:
    """Set the active provider."""
    from nexus.config import save_config
    from nexus.providers import get_manager

    config: NexusConfig = ctx.obj["config"]

    if name not in config.providers:
        click.echo(f"Provider not found: {name}", err=True)
        return

    config.active_provider = name
    save_config(config)

    manager = get_manager()
    manager.set_active(name)

    click.echo(f"Active provider set to: {name}")


# Model commands
@cli.group()
def model():
    """Manage AI models."""
    pass


@model.command("list")
@click.option("--provider", help="Filter by provider")
@click.pass_context
def model_list(ctx: click.Context, provider: str | None) -> None:
    """List available models."""

    async def run():
        manager = get_manager()
        try:
            models = await manager.list_models(provider)
            click.echo("Available models:\n")
            for m in models:
                click.echo(f"  {m.id}")
                click.echo(f"    Provider: {m.provider}")
                click.echo(f"    Context: {m.context_window:,} tokens")
                click.echo(f"    Vision: {'Yes' if m.supports_vision else 'No'}")
                click.echo()
        finally:
            await manager.close_all()

    asyncio.run(run())


@model.command("set")
@click.argument("model")
@click.option("--provider", help="Provider name")
@click.pass_context
def model_set(ctx: click.Context, model: str, provider: str | None) -> None:
    """Set the model for a provider."""

    async def run():
        manager = get_manager()
        try:
            await manager.switch_model(model, provider)
            p = provider or manager.active_provider
            click.echo(f"Model set to: {model} for provider: {p}")
        finally:
            await manager.close_all()

    asyncio.run(run())


@model.command("ping")
@click.argument("model_id", required=False)
@click.option("--provider", help="Provider name")
@click.option("--all", "all_models", is_flag=True, help="Ping all known models from this provider")
@click.option("--timeout", default=5, type=int, help="Timeout per model in seconds")
@click.pass_context
def model_ping(
    ctx: click.Context,
    model_id: str | None,
    provider: str | None,
    all_models: bool,
    timeout: int,
) -> None:
    """Ping a model to check accessibility.

    Tests whether a model is reachable and returns a valid response.
    Use --all to ping every known model from a provider simultaneously.

    Examples:

        nexus model ping gpt-4o

        nexus model ping --all --provider openai

        nexus model ping --provider groq --all
    """
    import asyncio

    from nexus.config import load_config
    from nexus.models.pinger import ModelPinger

    config = load_config()

    # Determine provider
    if not provider:
        provider = config.active_provider
    if not provider or provider not in config.providers:
        click.echo(f"  {_style('✘', 'red')} No active provider configured")
        return

    pcfg = config.providers[provider]
    api_key = pcfg.api_key
    base_url = pcfg.base_url or None

    if not api_key and provider != "ollama":
        click.echo(f"  {_style('✘', 'red')} No API key for {provider}")
        return

    # Determine models to ping
    from nexus.models import get_registry

    registry = get_registry()

    if all_models:
        click.echo(
            f"  {_style('Fetching model list from', 'cyan')} {_style(provider, 'white', bold=True)} ...\n"
        )
        models_list, _ = asyncio.run(
            registry.fetch_and_ping(provider, api_key, base_url, timeout)
        )
        if not models_list:
            click.echo(f"  {_style('⚠', 'yellow')} Could not fetch models for {provider}")
            click.echo(f"  {_style('Try:', 'bright_black')}  nexus model ping <specific-model>")
            return
        models = [m.id for m in models_list]
    elif model_id:
        models = [model_id]
    else:
        click.echo(f"  {_style('✘', 'red')} Specify a model or use --all")
        return

    click.echo(
        f"  {_style('Pinging', 'cyan')} {len(models)} model(s) "
        f"from {_style(provider, 'white', bold=True)} ...\n"
    )

    pinger = ModelPinger(concurrency=10)
    report = asyncio.run(
        pinger.ping_provider(provider, api_key, models, base_url, timeout)
    )

    for r in report.results:
        status = r.status_code or 0
        if r.ok:
            click.echo(
                f"  {_style('✔', 'green')}  {_style(r.model_id, bold=True):40s} "
                f"{_style(str(status), 'green'):6s}  {_style(f'{r.latency_ms:.0f}ms', 'bright_black')}"
            )
        else:
            click.echo(
                f"  {_style('✘', 'red')}  {_style(r.model_id, bold=True):40s} "
                f"{_style(str(status), 'red'):6s}  {_style(r.error[:40], 'yellow')}"
            )

    click.echo()
    working = report.working
    failed = report.failed
    total = len(report.results)
    click.echo(
        f"  {_style(f'{len(working)}/{total}', 'green', bold=True)} models accessible  "
        f"({_style(f'{report.total_time_ms:.0f}ms', 'bright_black')} total)"
    )

    if failed:
        click.echo()
        click.echo(
            f"  {_style('💡 Tip:', 'bright_black')}  "
            f"{_style('nexus model set <model> --provider ' + provider, 'cyan')} to switch"
        )


@model.command("discover")
@click.option("--provider", help="Provider name (default: active)")
@click.option("--timeout", default=5, type=int, help="Timeout per model")
@click.option("--set", "do_set", is_flag=True, help="Automatically set the first working model")
@click.pass_context
def model_discover(
    ctx: click.Context,
    provider: str | None,
    timeout: int,
    do_set: bool,
) -> None:
    """Discover which models are accessible for a provider.

    Pings every known model from the provider in parallel and reports
    which ones respond successfully. Like 'model ping --all' but with
    richer output and optional auto-select.

    Examples:

        nexus model discover

        nexus model discover --provider groq

        nexus model discover --provider openai --set
    """
    import asyncio

    from nexus.config import load_config, save_config
    from nexus.models.pinger import ModelPinger

    config = load_config()

    if not provider:
        provider = config.active_provider
    if not provider or provider not in config.providers:
        click.echo(f"  {_style('✘', 'red')} No active provider configured")
        return

    pcfg = config.providers[provider]
    api_key = pcfg.api_key
    base_url = pcfg.base_url or None

    if not api_key and provider != "ollama":
        click.echo(f"  {_style('✘', 'red')} No API key for {provider}")
        return

    from nexus.models import get_registry

    registry = get_registry()

    click.echo(f"\n  {_style('Fetching models from', 'cyan')} {_style(provider, 'white', bold=True)} API ...")

    models, ping_results = asyncio.run(
        registry.fetch_and_ping(provider, api_key, base_url, timeout)
    )

    if not models:
        click.echo(f"  {_style('⚠', 'yellow')} Could not fetch models for {provider}")
        click.echo(f"  {_style('Check your API key with:', 'bright_black')}  {_style('nexus auth check ' + provider, 'cyan')}")
        return

    working = [p for p in ping_results if p["ok"]]
    failed = [p for p in ping_results if not p["ok"]]

    click.echo(f"  {_style(f'Found {len(models)} models, {len(working)} accessible', 'bright_black')}\n")

    if not working:
        click.echo(f"  {_style('✘', 'red')} No accessible models found for {provider}")
        click.echo(f"  {_style('Check your API key with:', 'bright_black')}  {_style('nexus auth check ' + provider, 'cyan')}")
        return

    click.echo(f"  {_style('✔ Accessible models', 'green', bold=True)}")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")

    sorted_working = sorted(working, key=lambda x: x.get("latency_ms", 999))
    for r in sorted_working:
        ctx_w = ""
        pricing = ""
        for m in models:
            if m.id == r["model_id"]:
                if m.context_window:
                    ctx_w = f"{m.context_window:,} ctx"
                if m.pricing_input > 0:
                    pricing = f"${m.pricing_input:.2f}/${m.pricing_output:.2f}/M"
                break
        click.echo(
            f"    {_style('✔', 'green')}  {_style(r['model_id'], bold=True):45s} "
            f"  {_style(str(r['latency_ms']) + 'ms', 'bright_black'):8s}  "
            f"{_style(ctx_w, 'bright_black')}  {_style(pricing, 'yellow')}"
        )

    if failed:
        click.echo(f"\n  {_style(f'{len(failed)} models unreachable', 'bright_black')}")

    click.echo(f"\n  {_style(f'Scanned {len(models)} models from live API', 'bright_black')}")

    # Auto-set if requested
    if do_set and working:
        best = working[0]["model_id"]
        pcfg.model = best
        config.providers[provider] = pcfg
        save_config(config)
        click.echo(f"  {_style('✔', 'green')} Model set to: {_style(best, bold=True)}")


# Tool commands
@cli.group()
def tool():
    """Manage tools."""
    pass


@tool.command("list")
@click.option("--category", help="Filter by category")
@click.pass_context
def tool_list(ctx: click.Context, category: str | None) -> None:
    """List available tools."""
    registry = get_registry()

    if category:
        tools = registry.list_by_category(category)
        click.echo(f"Tools in '{category}':\n")
    else:
        tools = registry.list_all()
        click.echo(f"All tools ({len(tools)}):\n")
        for cat in registry.get_categories():
            click.echo(f"\n## {cat}")

    for t in tools:
        click.echo(f"\n### {t.name}")
        click.echo(f"   {t.description}")


# Session commands
@cli.group()
def session():
    """Manage sessions."""
    pass


@session.command("list")
@click.option("--limit", default=20, help="Number of sessions to show")
@click.pass_context
def session_list(ctx: click.Context, limit: int) -> None:
    """List recent sessions."""
    from ..memory import get_memory

    memory = get_memory()
    sessions = memory.list_sessions(limit)

    if not sessions:
        click.echo("No sessions found.")
        return

    click.echo("Recent sessions:\n")
    for s in sessions:
        date = s.created_at.strftime("%Y-%m-%d %H:%M")
        outcome = s.outcome or "in progress"
        click.echo(f"  [{date}] {s.id} - {outcome}")


@session.command("show")
@click.argument("session_id")
@click.pass_context
def session_show(ctx: click.Context, session_id: str) -> None:
    """Show a session's details."""
    from ..memory import get_memory

    memory = get_memory()
    session = memory.load_session(session_id)

    if not session:
        click.echo(f"Session not found: {session_id}", err=True)
        return

    click.echo(f"Session: {session.id}\n")
    click.echo(f"Created: {session.created_at}")
    click.echo(f"Updated: {session.updated_at}")
    click.echo(f"Outcome: {session.outcome or 'in progress'}")
    click.echo(f"Tools used: {', '.join(session.tools_used) if session.tools_used else 'none'}")
    click.echo(f"\nMessages: {len(session.messages)}")


# Memory commands
# Sync commands
@cli.group()
def sync():
    """Sync sessions across devices and services."""
    pass


@sync.command("status")
def sync_status() -> None:
    """Show sync status."""
    from ..sync import get_sync_engine

    engine = get_sync_engine()
    print(engine.format_status())


@sync.command("connect")
@click.argument("target_type")
@click.option("--name", default=None, help="Endpoint name")
@click.option("--token", help="API token (GitHub)")
@click.option("--path", type=click.Path(), help="Local path or git remote URL")
@click.option("--url", help="Service URL")
def sync_connect(target_type: str, name: str | None, token: str | None, path: str | None, url: str | None) -> None:
    """Connect a sync target (github-gist, local, git)."""
    from ..sync import SyncEndpoint, SyncTarget, get_sync_engine

    target_map = {
        "github-gist": SyncTarget.GITHUB_GIST,
        "github": SyncTarget.GITHUB_GIST,
        "local": SyncTarget.LOCAL,
        "git": SyncTarget.GIT_REMOTE,
    }

    target = target_map.get(target_type.lower())
    if not target:
        click.echo(f"Unknown target type: {target_type}. Valid: {', '.join(target_map.keys())}", err=True)
        return

    engine = get_sync_engine()
    endpoint_name = name or f"{target_type}-{target.value}"

    endpoint = SyncEndpoint(
        name=endpoint_name,
        target=target,
        token=token,
        path=Path(path) if path else None,
        url=url,
    )

    if engine.connect(endpoint):
        click.echo(f"Connected: {endpoint_name} ({target.name})")
    else:
        click.echo(f"Connection test failed for: {endpoint_name}", err=True)


@sync.command("push")
@click.argument("endpoint_name", default="default")
@click.option("--session", help="Specific session ID to push")
def sync_push(endpoint_name: str, session: str | None) -> None:
    """Push sessions to sync endpoint."""
    from ..sync import get_sync_engine

    engine = get_sync_engine()
    result = engine.push(endpoint_name, session)

    if result.get("success"):
        click.echo(f"Pushed: {result.get('items', 0)} item(s)")
        if result.get("gist_url"):
            click.echo(f"Gist: {result['gist_url']}")
    else:
        click.echo(f"Push failed: {result.get('error')}", err=True)


@sync.command("pull")
@click.argument("endpoint_name", default="default")
@click.option("--session", help="Specific session ID to pull")
def sync_pull(endpoint_name: str, session: str | None) -> None:
    """Pull sessions from sync endpoint."""
    from ..sync import get_sync_engine

    engine = get_sync_engine()
    result = engine.pull(endpoint_name, session)

    if result.get("success"):
        click.echo(f"Pulled: {result.get('items', 0)} item(s)")
        if result.get("conflicts"):
            click.echo(f"Conflicts: {', '.join(result['conflicts'])}")
    else:
        click.echo(f"Pull failed: {result.get('error')}", err=True)


@sync.command("disconnect")
@click.argument("endpoint_name")
def sync_disconnect(endpoint_name: str) -> None:
    """Disconnect a sync endpoint."""
    from ..sync import get_sync_engine

    engine = get_sync_engine()
    if engine.disconnect(endpoint_name):
        click.echo(f"Disconnected: {endpoint_name}")
    else:
        click.echo(f"Endpoint not found: {endpoint_name}", err=True)


# Learn commands
@cli.group()
def learn():
    """Failure learning and self-improvement."""
    pass


@learn.command("stats")
def learn_stats() -> None:
    """Show learning statistics."""
    from ..learn import get_learning_engine

    engine = get_learning_engine()
    print(engine.format_summary())


@learn.command("lessons")
@click.option("--show", default=5, help="Number of lessons to show")
def learn_lessons(show: int) -> None:
    """Show recent lessons."""
    from ..learn import get_learning_engine

    engine = get_learning_engine()
    lessons = engine._load_all_lessons()[:show]

    if not lessons:
        click.echo("No lessons yet. Keep working!")
        return

    for lesson in lessons:
        rate = lesson.success_count / max(1, lesson.success_count + lesson.failure_count)
        click.echo(f"\n  [{lesson.lesson_id}] {lesson.title}")
        click.echo(f"    {lesson.summary[:100]}...")
        click.echo(f"    Success rate: {rate:.0%} ({lesson.success_count} ok / {lesson.failure_count} fail)")
        for tc in lesson.trigger_conditions[:3]:
            click.echo(f"    Trigger: {tc}")


@learn.command("failures")
@click.option("--limit", default=10, help="Number of failures to show")
def learn_failures(limit: int) -> None:
    """Show recent failure records."""
    import json

    from ..learn import get_learning_engine

    engine = get_learning_engine()
    failures = sorted(
        engine.failures_dir.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:limit]

    if not failures:
        click.echo("No failures recorded.")
        return

    for f in failures:
        data = json.loads(f.read_text())
        date = data["timestamp"][:16]
        click.echo(f"\n  [{date}] {data['tool_name']} — {data['error_type']}")
        click.echo(f"    {data['error'][:80]}...")
        if data.get("resolution"):
            click.echo(f"    Resolved: {data['resolution'][:60]}")


@learn.command("clear")
@click.confirmation_option(prompt="Clear all failure records and lessons?")
def learn_clear() -> None:
    """Clear all learning data."""

    from ..learn import get_learning_engine

    engine = get_learning_engine()

    for d in [engine.failures_dir, engine.lessons_dir, engine.patterns_dir]:
        if d.exists():
            for f in d.glob("*.json"):
                f.unlink()
            for f in d.glob("*.md"):
                f.unlink()

    click.echo("Learning data cleared.")


# Memory commands
@cli.group()
def memory():
    """Manage memory."""
    pass


@memory.command("facts")
@click.pass_context
def memory_facts(ctx: click.Context) -> None:
    """Show stored facts."""
    from ..memory import get_memory

    memory = get_memory()
    facts = memory.get_all_facts()

    if not facts:
        click.echo("No facts stored.")
        return

    click.echo("Stored facts:\n")
    for key, value in facts.items():
        click.echo(f"  {key}: {value}")


@memory.command("add-fact")
@click.argument("key")
@click.argument("value")
@click.option("--category", default="general", help="Fact category")
@click.pass_context
def memory_add_fact(ctx: click.Context, key: str, value: str, category: str) -> None:
    """Add a fact to memory."""
    from ..memory import get_memory

    memory = get_memory()
    memory.add_fact(key, value, category)
    click.echo(f"Added fact: {key} = {value}")


# Settings commands
@cli.group()
def config():
    """Manage configuration."""
    pass


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Show current configuration."""
    cfg: NexusConfig = ctx.obj["config"]
    import json

    click.echo(json.dumps(cfg.to_dict(), indent=2, default=str))


@config.command("set")
@click.argument("key")
@click.argument("value")
@click.pass_context
def config_set(ctx: click.Context, key: str, value: str) -> None:
    """Set a configuration value."""
    config: NexusConfig = ctx.obj["config"]

    # Handle nested keys like 'providers.openai.model'
    if "." in key:
        parts = key.split(".")
        obj = config.to_dict()
        for p in parts[:-1]:
            obj = obj[p]
        obj[parts[-1]] = value
        new_config = NexusConfig.from_dict(config.to_dict())
        new_config.ensure_dirs()
        save_config(new_config)
    else:
        setattr(config, key, value)
        save_config(config)

    click.echo(f"Set {key} = {value}")


# Setup command
OPENCODE_ZEN_FREE_MODELS = [
    ("minimax-m2.5-free", "MiniMax M2.5 — Best all-around, fastest", "free"),
    ("big-pickle", "Big Pickle — OpenCode's own model", "free"),
    ("qwen3.6-plus-free", "Qwen 3.6 Plus — Large context window", "free"),
    ("nemotron-3-super-free", "Nemotron 3 Super — NVIDIA's best free", "free"),
]

PROVIDER_OPTIONS = [
    (
        "opencode-zen",
        "OpenCode Zen",
        "Recommended",
        "Best free models, no key needed for free tier",
    ),
    ("opencode-go", "OpenCode Go", "Premium", "Kimi K2.5, GLM 5, MiniMax M2.7 (paid)"),
    ("groq", "Groq", "Free tier", "Llama-3.3-70B, Mixtral — fast inference"),
    ("openrouter", "OpenRouter", "100+ models", "Access to dozens of providers, has free models"),
    ("anthropic", "Anthropic", "Premium", "Claude Sonnet 4, Opus 4 — best reasoning"),
    ("openai", "OpenAI", "Premium", "GPT-4o, GPT-4o-mini — reliable"),
    ("google", "Google Gemini", "Free + Paid", "Gemini 2.0 Flash — fast, good free tier"),
    ("ollama", "Ollama", "Local", "Run models locally on your machine (private)"),
]

API_KEY_INSTRUCTIONS = {
    "opencode-zen": "Get your free key at https://opencode.ai/zen (optional for free models)",
    "opencode-go": "Get your subscription key at https://opencode.ai/zen/go",
    "groq": "Get free key at https://console.groq.com/keys",
    "openrouter": "Get key at https://openrouter.ai/keys",
    "anthropic": "Get key at https://console.anthropic.com/settings/keys",
    "openai": "Get key at https://platform.openai.com/api-keys",
    "google": "Get key at https://aistudio.google.com/app/apikey",
    "ollama": "No key needed — runs locally (run: ollama serve)",
}


@cli.command("setup")
@click.option("--provider", help="Provider name (skip interactive mode)")
@click.option("--model", help="Model name (skip interactive mode)")
@click.option("--api-key", help="API key (skip interactive mode)")
@click.option("--non-interactive", is_flag=True, help="Use defaults or env vars (for CI)")
@click.pass_context
def setup_cmd(
    ctx: click.Context,
    provider: str | None,
    model: str | None,
    api_key: str | None,
    non_interactive: bool,
) -> None:
    """Interactive setup wizard — configure everything in one session.

    A guided, 10-step journey through personalisation, provider selection,
    API key configuration, model picking, connection testing, tool profile,
    search provider, plugins, skills, and fine-tuning.

    Examples:

        nexus setup                        Interactive wizard

        nexus setup --non-interactive      Use env vars or defaults

        nexus setup --provider groq         Quick setup for a specific provider

        nexus setup --provider openai --model gpt-4o-mini --api-key sk-...
    """
    config: NexusConfig = ctx.obj["config"]

    from .onboarding import OnboardingManager

    manager = OnboardingManager(config, non_interactive=non_interactive)
    ok = manager.run(
        provider=provider,
        model=model,
        api_key=api_key,
    )

    if not ok:
        sys.exit(1)


def print_cheatsheet(provider: str, model: str, is_termux: bool) -> None:
    click.echo("=" * 50)
    click.echo("  QUICK REFERENCE")
    click.echo("=" * 50)
    click.echo("")
    click.echo("  # Start chatting")
    if is_termux:
        click.echo("  nexus repl")
    else:
        click.echo("  nexus repl")
    click.echo("")
    click.echo("  # Run a single task")
    click.echo('  nexus run "Fix the login bug"')
    click.echo("")
    click.echo("  # Dashboard (optional web UI)")
    click.echo("  nexus dashboard")
    click.echo("")
    click.echo("  # Voice mode (speak to Nexus)")
    click.echo("  nexus voice")
    click.echo("")
    click.echo("  # Help")
    click.echo("  nexus repl  # then type /help")
    click.echo("")
    click.echo("  Current: " + provider + " / " + model)
    click.echo("")


# Doctor command
@cli.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check system health and diagnose issues."""
    from ..config import DEFAULT_CONFIG_DIR

    click.echo("[*] Running diagnostics...\n")

    issues = []

    # Check config directory
    if not DEFAULT_CONFIG_DIR.exists():
        issues.append(f"Config directory missing: {DEFAULT_CONFIG_DIR}")
        click.echo("  [-] Config directory missing")
    else:
        click.echo("  [+] Config directory exists")

    # Check config file
    from ..config import DEFAULT_CONFIG_FILE

    if not DEFAULT_CONFIG_FILE.exists():
        issues.append(f"Config file missing: {DEFAULT_CONFIG_FILE}")
        click.echo("  [-] Config file missing")
    else:
        click.echo("  [+] Config file exists")

    # Check providers
    config: NexusConfig = ctx.obj["config"]
    if not config.providers:
        issues.append("No providers configured")
        click.echo("  [-] No providers configured")
    else:
        click.echo(f"  [+] {len(config.providers)} provider(s) configured")

    # Check for common tools
    import shutil

    for tool in ["git", "python", "pip"]:
        if shutil.which(tool):
            click.echo(f"  [+] {tool} found")
        else:
            issues.append(f"{tool} not found in PATH")
            click.echo(f"  [-] {tool} not found")

    # Check for ripgrep
    if shutil.which("rg"):
        click.echo("  [+] ripgrep found (for search)")
    else:
        click.echo("  [!] ripgrep not found (install with: pip install ripgrep)")

    # Check learning system
    try:
        from ..learn import get_learning_engine

        le = get_learning_engine()
        stats = le.get_stats()
        click.echo(f"  [+] Learning engine: {stats['total_lessons']} lessons, {stats['total_failures']} failures")
    except Exception as e:
        click.echo(f"  [!] Learning engine error: {e}")

    # Check sync system
    try:
        from ..sync import get_sync_engine

        se = get_sync_engine()
        status = se.get_status()
        ep_count = len(status.get("endpoints", {}))
        click.echo(f"  [+] Sync engine: {ep_count} endpoint(s) configured")
    except Exception as e:
        click.echo(f"  [!] Sync engine error: {e}")

    # Check safety engine
    try:
        from ..safety import get_safety_engine

        se = get_safety_engine()
        click.echo(f"  [+] Safety engine: {len(se.rules)} rules loaded")
    except Exception as e:
        click.echo(f"  [!] Safety engine error: {e}")

    # Check self-improvement
    try:
        from ..self_improve import get_self_improver

        si = get_self_improver()
        pending = len(si.get_improvement_queue())
        click.echo(f"  [+] Self-improvement: {pending} improvement(s) pending")
    except Exception as e:
        click.echo(f"  [!] Self-improvement error: {e}")

    # Check phone mode
    try:
        from ..phone import get_phone_mode

        pm = get_phone_mode()
        click.echo(f"  [+] Phone mode: {pm.profile.name} profile (auto-detected)")
    except Exception as e:
        click.echo(f"  [!] Phone mode error: {e}")

    # Check voice system
    try:

        engine = get_voice_engine()
        voices = list_tts_voices()
        click.echo(f"  [+] Voice: TTS={engine.config.tts_provider} ({len(voices)} voices), STT={engine.config.stt_provider}")
    except Exception as e:
        click.echo(f"  [!] Voice system error: {e}")

    if issues:
        click.echo(f"\n[!] {len(issues)} issue(s) found:")
        for issue in issues:
            click.echo(f"  - {issue}")
        click.echo("\n[*] Fix these issues by running: \033[1mnexus setup\033[0m")
    else:
        click.echo("\n[+] All checks passed!")


from ..utils.dependencies import ensure_dependency


# Dashboard command (optional — lazy loaded)
@cli.command("dashboard")
@click.option("--port", default=5000, help="Port to run on")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--open/--no-open", default=True, help="Open browser automatically")
@click.pass_context
def dashboard(ctx: click.Context, port: int, host: str, open: bool) -> None:
    """Launch the optional web dashboard (lazy-loaded).

    The dashboard provides a visual overview of sessions, stats, and provider status.
    Run 'nexus dashboard --help' for options.
    """
    if not ensure_dependency("flask"):
        return

    from ..dashboard.app import create_app

    app = create_app()

    if open:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{host}:{port}")).start()

    click.echo(f"Dashboard starting on http://{host}:{port}")
    click.echo("Press Ctrl+C to stop")
    app.run(host=host, port=port, debug=False)


# TUI command
@cli.command()
@click.pass_context
def tui(ctx: click.Context) -> None:
    """Launch the rich Textual TUI."""
    if not ensure_dependency("textual"):
        return

    from .welcome import display_welcome

    display_welcome()

    from ..tui.app import NexusTUI

    app = NexusTUI()
    app.run()


# Voice command
@cli.command()
@click.option("--tts", "tts_override", help="TTS provider override (e.g., freetts, openai)")
@click.option("--stt", "stt_override", help="STT provider override (e.g., whisper, assemblyai)")
@click.option("--voice", "voice_override", help="Voice name (e.g., en-US-Neural2-F)")
@click.option("--continuous", is_flag=True, default=False, help="Keep listening after each response")
@click.pass_context
def voice(
    ctx: click.Context,
    tts_override: str | None,
    stt_override: str | None,
    voice_override: str | None,
    continuous: bool,
) -> None:
    """Enter voice mode — Nexus speaks and listens like a partner.

    This starts an interactive voice conversation where Nexus responds
    using text-to-speech and listens via speech-to-text. Works with
    microphone and speakers.

    Examples:

        nexus voice                     Start with defaults
        nexus voice --tts freetts        Use FreeTTS (no API key needed)
        nexus voice --stt whisper       Use local Whisper for STT
        nexus voice --voice en-US-Neural2-F  Use a specific voice
        nexus voice --continuous        Keep listening after each response

    Provider options:
      TTS: freetts (default, no key), openai, espeak, pico
      STT: assemblyai, deepgram, whisper, freetts
    """
    if not ensure_dependency("pyaudio"):
        return

    if stt_override == "whisper" or (not stt_override and "whisper" in str(ctx.obj.get("config", {}))):
        if not ensure_dependency("faster-whisper"):
            return

    from .welcome import display_welcome

    display_welcome()

    import asyncio

    from ..personality import get_personality

    async def _run():
        overrides = {}
        if tts_override:
            overrides["tts_provider"] = tts_override
        if stt_override:
            overrides["stt_provider"] = stt_override
        if voice_override:
            overrides["voice"] = voice_override

        engine = get_voice_engine(**overrides)
        personality = get_personality()

        click.echo(f"\n{engine.config.tts_provider.upper()} | {engine.config.stt_provider.upper()}")
        click.echo("Say something or press Ctrl+C to exit...\n")

        async def _llm_callback(text: str) -> str:
            from ..personality import get_personality
            from ..providers import get_manager
            from ..tools import get_registry

            manager = get_manager()
            registry = get_registry()
            personality = get_personality()

            tools = [t.to_definition() for t in registry.list_all()]
            system_msg = Message(
                role="system",
                content=personality.get_voice_system_prompt(),
            )
            messages = [system_msg, Message(role="user", content=text)]

            try:
                resp = await manager.complete(messages, tools)
                return resp.content or "I didn't get a response."
            except Exception as e:
                return f"Oops: {e}"
            finally:
                await manager.close_all()

        engine.llm_callback = _llm_callback

        await engine.speak(personality.greet())

        async with engine.voice_mode():
            if continuous:
                while engine._running:
                    await asyncio.sleep(0.5)
            else:
                await engine.listen_and_transcribe()
                if engine.last_transcription:
                    response = await _llm_callback(engine.last_transcription)
                    await engine.speak(response)

    asyncio.run(_run())


# REPL command
@cli.command("repl")
@click.pass_context
def repl(ctx: click.Context) -> None:
    """Start an interactive REPL session.

    The REPL provides an interactive chat interface with Nexus.
    Use /help inside the REPL for available slash commands.
    """
    from .welcome import display_welcome

    display_welcome()

    from ..cli.repl import run_repl
    from ..config import load_config

    config = load_config()
    config_dict = {
        "providers": {k: v.to_dict() for k, v in config.providers.items()},
        "active_provider": config.active_provider,
        "config_dir": str(config.config_dir),
    }
    asyncio.run(run_repl(config=config_dict))


# Run command (single task)
@cli.command("run")
@click.argument("task")
@click.pass_context
def run(ctx: click.Context, task: str) -> None:
    """Run a single task and exit.

    Useful for scripting and one-shot automation tasks.
    """
    from ..cli.repl import run_task
    from ..config import load_config

    config = load_config()
    config_dict = {
        "providers": {k: v.to_dict() for k, v in config.providers.items()},
        "active_provider": config.active_provider,
        "config_dir": str(config.config_dir),
    }
    result, was_streamed = asyncio.run(run_task(task, config=config_dict))
    if result and not was_streamed:
        click.echo(result)


# Automation commands
@cli.group()
def automation():
    """Browser and API automation tools."""
    pass


@automation.command("status")
def automation_status() -> None:
    """Check automation system status."""
    from ..automation import is_browser_available
    from ..automation.browser import PLAYWRIGHT_AVAILABLE

    click.echo("\nAutomation Status:\n")

    click.echo(f"  Playwright:     {'[+] installed' if PLAYWRIGHT_AVAILABLE else '[-] not installed'}")
    click.echo(f"  Chromium:       {'[+] available' if is_browser_available() else '[-] not installed (run: nexus automation install-browser)'}")

    if PLAYWRIGHT_AVAILABLE:
        click.echo("\n  Browser:        Configured for stealth/anti-detection")
        click.echo("  User-Agent:     Randomized rotation enabled")
        click.echo("  CAPTCHA detect: Built-in (recaptcha, hcaptcha, cloudflare)")
        click.echo("  Human-like:    Mouse curves, keystroke delays, scroll")

    click.echo("\n  API Client:     httpx (always available)")
    click.echo("  Rate limiting:  1-3s delay between requests")
    click.echo("  Header rotate:  Referrer, Sec-Fetch, Accept-Language\n")


@automation.command("install-browser")
@click.option("--browser", default="chromium", help="Browser to install (chromium, firefox, webkit)")
@click.option("--with-deps", is_flag=True, default=False, help="Install system dependencies")
def automation_install_browser(browser: str, with_deps: bool) -> None:
    """Install browser for automation. Run this once on a new machine."""
    from ..automation.browser import PLAYWRIGHT_AVAILABLE

    if not PLAYWRIGHT_AVAILABLE:
        click.echo("Playwright not installed. Run: pip install playwright")
        return

    click.echo(f"Installing {browser}...")
    import subprocess

    cmd = ["playwright", "install"]
    if with_deps:
        cmd.append("--with-deps")
    cmd.append(browser)

    result = subprocess.run(cmd)
    if result.returncode == 0:
        click.echo(f"[+] {browser} installed successfully")
    else:
        click.echo(f"[-] Installation failed (exit code: {result.returncode})")
        click.echo(f"  Try: playwright install {browser} --with-deps")


# MCP commands
@cli.group()
def mcp():
    """Manage MCP (Model Context Protocol) servers."""
    pass


@mcp.command("list")
def mcp_list():
    """List configured MCP servers and their tools."""
    from nexus.mcp import MCPClient, MCPServerConfig

    client = MCPClient()
    config_path = Path.home() / ".nexus" / "mcp-servers.json"
    if config_path.exists():
        import json
        servers = json.loads(config_path.read_text())
        for name, cfg in servers.items():
            click.echo(f"\n  {name} ({cfg.get('transport', 'stdio')})")
            click.echo(f"    Command: {cfg.get('command', cfg.get('url', 'N/A'))}")
            try:
                client.add_server(MCPServerConfig(name=name, **cfg))
                import asyncio
                asyncio.run(client.initialize_server(name))
                tools = [t for t in client.list_tools() if t.server_name == name]
                for t in tools:
                    click.echo(f"    → {t.name}: {t.description or 'No description'}")
            except Exception as e:
                click.echo(f"    Error: {e}")
    else:
        click.echo("No MCP servers configured.")
        click.echo(f"  Create {config_path} or use 'nexus mcp add'.")


@mcp.command("add")
@click.argument("name")
@click.option("--command", help="Command to run (stdio transport)")
@click.option("--args", help="Space-separated arguments")
@click.option("--url", help="URL for SSE transport")
@click.option("--transport", default="stdio", type=click.Choice(["stdio", "sse"]))
def mcp_add(name: str, command: str | None, args: str | None, url: str | None, transport: str):
    """Add an MCP server configuration."""
    import json

    config_path = Path.home() / ".nexus" / "mcp-servers.json"
    servers = {}
    if config_path.exists():
        servers = json.loads(config_path.read_text())

    cfg = {"transport": transport}
    if transport == "stdio":
        if not command:
            click.echo("--command is required for stdio transport", err=True)
            return
        cfg["command"] = command
        if args:
            cfg["args"] = args.split()
    else:
        if not url:
            click.echo("--url is required for SSE transport", err=True)
            return
        cfg["url"] = url

    servers[name] = cfg
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(servers, indent=2))
    click.echo(f"Added MCP server: {name}")


@mcp.command("remove")
@click.argument("name")
def mcp_remove(name: str):
    """Remove an MCP server configuration."""
    import json

    config_path = Path.home() / ".nexus" / "mcp-servers.json"
    if not config_path.exists():
        click.echo("No MCP servers configured.", err=True)
        return

    servers = json.loads(config_path.read_text())
    if name in servers:
        del servers[name]
        config_path.write_text(json.dumps(servers, indent=2))
        click.echo(f"Removed MCP server: {name}")
    else:
        click.echo(f"MCP server not found: {name}", err=True)


# Exec command
@cli.command("exec")
@click.argument("command", nargs=-1, required=True)
@click.option("--timeout", default=60, help="Timeout in seconds")
@click.option("--shell/--no-shell", default=True, help="Run via shell")
def exec_cmd(command: tuple[str, ...], timeout: int, shell: bool) -> None:
    """Execute a shell command and display output.

    Runs the given command in a subprocess and shows stdout/stderr in real-time.
    Use --no-shell to avoid shell interpretation (safer for dynamic args).
    """
    import asyncio
    import shlex

    cmd_str = " ".join(command) if shell else list(command)
    click.echo(f"\n\033[36m\u25B6 Executing: {cmd_str}\033[0m\n")

    async def _run():
        proc = await asyncio.create_subprocess_shell(
            cmd_str if shell else cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            if stdout:
                click.echo(stdout.decode())
            if stderr:
                click.echo(f"\033[33m{stderr.decode()}\033[0m")
            if proc.returncode == 0:
                click.echo(f"\033[32m\u2713 Exit code: {proc.returncode}\033[0m")
            else:
                click.echo(f"\033[31m\u2717 Exit code: {proc.returncode}\033[0m")
        except asyncio.TimeoutError:
            proc.kill()
            click.echo(f"\033[31m\u2717 Command timed out after {timeout}s\033[0m")

    asyncio.run(_run())


# Skill commands
@cli.group()
def skill():
    """Manage skills (local and community)."""
    pass


@skill.command("list")
@click.option("--category", help="Filter by category")
def skill_list(category: str | None) -> None:
    """List installed skills."""
    from nexus.skills import SkillsManager

    sm = SkillsManager()
    sm.load_all()
    skills = sm.list_all()
    if not skills:
        click.echo("No skills installed.")
        click.echo("Use 'nexus skill search <query>' to find community skills.")
        return

    if category:
        skills = [s for s in skills if s.category == category]

    click.echo(f"\n\033[36mInstalled Skills ({len(skills)})\033[0m\n")
    active = sm.list_active()
    for s in skills:
        act = "\033[32m\u25CF\033[0m" if s.name in active else "\033[90m\u25CB\033[0m"
        click.echo(f"  {act} \033[1m{s.name}\033[0m  \033[90m{s.version}\033[0m")
        click.echo(f"      {s.description}")
        click.echo(f"      \033[90mcategory: {s.category}  tags: {', '.join(s.tags)}\033[0m")


@skill.command("search")
@click.argument("query", required=True)
def skill_search(query: str) -> None:
    """Search the community skill registry."""
    from nexus.skills import SkillsManager

    sm = SkillsManager()
    results = sm.search_community(query)
    if not results:
        click.echo(f"No community skills matching '{query}'.")
        click.echo("The registry is at https://github.com/alpha-1-design/nexus-skills")
        click.echo("You can contribute your own skills there!")
        return

    click.echo(f"\n\033[36mCommunity Skills matching '{query}' ({len(results)})\033[0m\n")
    for s in results[:20]:
        tags = s.get("tags", [])
        tag_str = f" \033[90m{', '.join(tags[:3])}\033[0m" if tags else ""
        click.echo(f"  \033[1m{s.get('name')}\033[0m  \033[90mv{s.get('version', '1.0')}\033[0m")
        click.echo(f"      {s.get('description', 'No description')}{tag_str}")
    click.echo(f"\nInstall with: \033[36mnexus skill install <name>\033[0m")


@skill.command("install")
@click.argument("name", required=True)
def skill_install(name: str) -> None:
    """Install a community skill."""
    from nexus.skills import SkillsManager

    click.echo(f"Installing '{name}' from community registry...")
    sm = SkillsManager()
    sm.load_all()
    result = sm.install_community(name)
    if result["status"] == "success":
        click.echo(f"\033[32m\u2713 {result['message']}\033[0m")
        click.echo(f"  Path: \033[90m{result.get('path')}\033[0m")
        click.echo("  The skill is now active in this session.")
    else:
        click.echo(f"\033[31m\u2717 {result['message']}\033[0m")


@skill.command("uninstall")
@click.argument("name", required=True)
def skill_uninstall(name: str) -> None:
    """Uninstall a skill."""
    from nexus.skills import SkillsManager

    sm = SkillsManager()
    sm.load_all()
    result = sm.uninstall(name)
    if result["status"] == "success":
        click.echo(f"\033[32m\u2713 {result['message']}\033[0m")
    else:
        click.echo(f"\033[31m\u2717 {result['message']}\033[0m")


# =============================================================================
# init — initialize Nexus in a project
# =============================================================================


@cli.command("init")
@click.option("--force", is_flag=True, help="Overwrite existing configuration")
@click.option("--scan/--no-scan", default=True, help="Scan codebase after init")
@click.argument("path", type=click.Path(), default=".", required=False)
def init_cmd(path: str, force: bool, scan: bool) -> None:
    """Initialize Nexus in a project directory.

    Creates .nexus/config.json with project metadata, scans the codebase
    to detect languages and frameworks, and generates an initial config.

    Example:

        nexus init                     # init current directory

        nexus init /path/to/project    # init specific project

        nexus init --force             # overwrite existing config
    """
    from nexus.project import ProjectInitializer

    click.echo(f"\n  \033[36m\u25B6 Initializing Nexus in:\033[0m \033[1m{path}\033[0m\n")
    init = ProjectInitializer()
    result = init.initialize(path=path, force=force, scan=scan)

    if result.get("status") == "success":
        click.echo(f"  \033[32m\u2713 {result['message']}\033[0m")
        for key, val in result.get("details", {}).items():
            click.echo(f"    \033[90m{key}: {val}\033[0m")
    else:
        click.echo(f"  \033[31m\u2717 {result.get('message', 'Init failed')}\033[0m")


# =============================================================================
# status — quick overview
# =============================================================================


@cli.command("status")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def status_cmd(json_output: bool) -> None:
    """Show current Nexus status at a glance.

    Displays active provider, model, configuration health, session info,
    and system resources.

    Example:

        nexus status

        nexus status --json
    """
    import json as _json
    import platform
    import subprocess as _sp
    from datetime import datetime

    from nexus.config import load_config

    config = load_config()

    # Provider / model info
    active_provider = config.active_provider or "none"
    active_model = "unknown"
    provider_type = "unknown"

    if active_provider != "none" and active_provider in config.providers:
        p = config.providers[active_provider]
        active_model = getattr(p, "model", "unknown") or "unknown"
        provider_type = getattr(p, "provider_type", "") or ""

    # Session info
    session_count = 0
    try:
        session_dir = Path.home() / ".nexus" / "sessions"
        if session_dir.exists():
            session_count = len(list(session_dir.glob("*.json")))
    except Exception:
        pass

    # Git info
    git_branch = "N/A"
    git_sha = "N/A"
    try:
        branch = _sp.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2
        )
        if branch.returncode == 0:
            git_branch = branch.stdout.strip()
        sha = _sp.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2
        )
        if sha.returncode == 0:
            git_sha = sha.stdout.strip()
    except Exception:
        pass

    from nexus import __version__ as ver

    data = {
        "version": ver,
        "active_provider": active_provider,
        "model": active_model,
        "provider_type": provider_type,
        "providers_configured": len(config.providers),
        "sessions": session_count,
        "git_branch": git_branch,
        "git_sha": git_sha,
        "python": platform.python_version(),
        "platform": platform.system(),
        "config_file": str(config._config_path) if hasattr(config, "_config_path") else "N/A",
        "timestamp": datetime.now().isoformat(),
    }

    if json_output:
        click.echo(_json.dumps(data, indent=2))
        return

    c = lambda t, **kw: click.style(t, **kw)  # noqa: E731

    click.echo()
    click.echo(f"  {c('N E X U S', bold=True)}  {c(ver, fg='cyan')}")
    click.echo(f"  {c('\u2500' * 50, fg='bright_black')}")
    click.echo(f"  {c('Provider', bold=True)}    {c(active_provider, fg='green')} {c(provider_type, fg='bright_black')}")
    click.echo(f"  {c('Model', bold=True)}       {c(active_model, fg='white')}")
    click.echo(f"  {c('Sessions', bold=True)}    {c(str(session_count), fg='white')}")
    click.echo(f"  {c('Git', bold=True)}         {c(git_branch, fg='blue')} {c(git_sha, fg='bright_black')}")
    click.echo(f"  {c('Python', bold=True)}      {platform.python_version()}")
    click.echo(f"  {c('Platform', bold=True)}    {platform.system()}")
    click.echo(f"  {c('Config', bold=True)}      {data['config_file']}")
    click.echo()


# =============================================================================
# auth — API key management
# =============================================================================


@cli.group("auth")
def auth():
    """Manage API keys and authentication.

    Add, list, remove, and verify API keys for LLM providers.

    Example:

        nexus auth list

        nexus auth add openai --key sk-...

        nexus auth remove openai

        nexus auth check openai
    """
    pass


@auth.command("list")
def auth_list() -> None:
    """List configured providers and their key status."""
    from nexus.config import load_config

    config = load_config()

    if not config.providers:
        click.echo("  \033[33mNo providers configured.\033[0m")
        click.echo("  Run \033[36mnexus setup\033[0m or \033[36mnexus auth add <provider>\033[0m")
        return

    click.echo(f"\n  \033[1mConfigured Providers\033[0m ({len(config.providers)})")
    click.echo(f"  \033[90m{'=' * 40}\033[0m")
    for name, p in config.providers.items():
        active = "\033[32m\u25CF\033[0m" if name == config.active_provider else "\033[90m\u25CB\033[0m"
        key_configured = "\033[32mkey set\033[0m" if getattr(p, "api_key", None) else "\033[31mno key\033[0m"
        click.echo(f"  {active} \033[1m{name}\033[0m")
        click.echo(f"      {getattr(p, 'provider_type', '?')} / {getattr(p, 'model', '?')}  [{key_configured}]")
    click.echo()


@auth.command("add")
@click.argument("provider_name")
@click.option("--key", "--api-key", "api_key", help="API key (omit for prompt)")
@click.option("--model", default="gpt-4o", help="Default model")
@click.option("--base-url", help="Base URL for API (for custom endpoints)")
@click.option("--provider-type", help="Provider type (e.g., openai, groq, anthropic)")
@click.option("--set-active/--no-set-active", default=True, help="Set as active provider")
def auth_add(
    provider_name: str,
    api_key: str | None,
    model: str,
    base_url: str | None,
    provider_type: str | None,
    set_active: bool,
) -> None:
    """Add or update a provider's API key and configuration."""
    from nexus.config import ProviderConfig, load_config, save_config

    config = load_config()

    # Determine provider type
    ptype = provider_type or provider_name
    key = api_key

    # If no key flag, prompt securely
    while not key:
        key = click.prompt(
            f"  API key for {provider_name}",
            hide_input=True,
            default="",
            show_default=False,
        )
        if not key:
            # Let the provider type auto-detect key from env
            env_key = _detect_env_key(ptype)
            if env_key:
                key = env_key
                click.echo(f"  \033[90mUsing key from {ptype.upper()}_API_KEY env var\033[0m")
                break
            click.echo("  \033[33mKey cannot be empty. Press Ctrl+C to cancel.\033[0m")

    cfg = ProviderConfig(
        name=provider_name,
        provider_type=ptype,
        model=model,
        api_key=key,
        base_url=base_url or "",
    )

    config.providers[provider_name] = cfg
    if set_active:
        config.active_provider = provider_name

    save_config(config)

    click.echo(f"  \033[32m\u2713 Provider '{provider_name}' configured\033[0m")
    if set_active:
        click.echo(f"  \033[90mSet as active provider\033[0m")


@auth.command("remove")
@click.argument("provider_name")
def auth_remove(provider_name: str) -> None:
    """Remove a provider configuration."""
    from nexus.config import load_config, save_config

    config = load_config()

    if provider_name not in config.providers:
        click.echo(f"  \033[31m\u2717 Provider '{provider_name}' not found\033[0m")
        return

    was_active = provider_name == config.active_provider
    del config.providers[provider_name]

    if was_active:
        if config.providers:
            config.active_provider = next(iter(config.providers))
        else:
            config.active_provider = ""

    save_config(config)
    click.echo(f"  \033[32m\u2713 Removed '{provider_name}'\033[0m")
    if was_active and config.active_provider:
        click.echo(f"  \033[90mActive provider switched to '{config.active_provider}'\033[0m")


@auth.command("check")
@click.argument("provider_name", required=False)
def auth_check(provider_name: str | None) -> None:
    """Verify API key connectivity by making a test call."""
    from nexus.config import load_config

    config = load_config()

    if provider_name:
        providers_to_check = (
            [provider_name] if provider_name in config.providers else []
        )
        if not providers_to_check:
            click.echo(f"  \033[31m\u2717 Provider '{provider_name}' not found\033[0m")
            return
    else:
        providers_to_check = list(config.providers.keys())
        if not providers_to_check:
            click.echo("  \033[33mNo providers configured.\033[0m")
            return

    import httpx

    for name in providers_to_check:
        p = config.providers[name]
        ptype = getattr(p, "provider_type", name)

        click.echo(f"  Checking \033[1m{name}\033[0m (\033[90m{ptype}\033[0m)... ", nl=False)

        # Try a minimal API call to verify
        try:
            url = getattr(p, "base_url", "") or _default_api_url(ptype)
            key = getattr(p, "api_key", "")
            headers = {"Authorization": f"Bearer {key}"}
            resp = httpx.get(
                url.rstrip("/") + "/models",
                headers=headers,
                timeout=5,
            )
            if resp.status_code == 200:
                click.echo(f"\033[32m\u2713 OK\033[0m")
            elif resp.status_code == 401:
                click.echo(f"\033[31m\u2717 Invalid key\033[0m")
            else:
                click.echo(f"\033[33m\u26A0 HTTP {resp.status_code}\033[0m")
        except httpx.ConnectError:
            click.echo(f"\033[31m\u2717 Connection failed\033[0m")
        except Exception as e:
            click.echo(f"\033[31m\u2717 {e}\033[0m")


def _default_api_url(provider_type: str) -> str:
    """Return the default API base URL for a provider type."""
    urls = {
        "openai": "https://api.openai.com/v1",
        "groq": "https://api.groq.com/openai/v1",
        "anthropic": "https://api.anthropic.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "together": "https://api.together.xyz/v1",
        "mistral": "https://api.mistral.ai/v1",
        "cohere": "https://api.cohere.ai/v1",
    }
    return urls.get(provider_type.lower(), "https://api.openai.com/v1")


def _detect_env_key(provider_type: str) -> str | None:
    """Detect API key from common environment variables."""
    import os as _os
    env_map = {
        "openai": ("OPENAI_API_KEY", "OPENAI_KEY"),
        "groq": ("GROQ_API_KEY", "GROQ_KEY"),
        "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY"),
        "openrouter": ("OPENROUTER_API_KEY", "OPENROUTER_KEY"),
        "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "deepseek": ("DEEPSEEK_API_KEY", "DEEPSEEK_KEY"),
        "together": ("TOGETHER_API_KEY", "TOGETHER_KEY"),
        "mistral": ("MISTRAL_API_KEY", "MISTRAL_KEY"),
        "cohere": ("COHERE_API_KEY", "COHERE_KEY"),
    }
    for var in env_map.get(provider_type.lower(), []):
        val = _os.environ.get(var)
        if val:
            return val
    return None


# =============================================================================
# logs — view Nexus logs
# =============================================================================


@cli.command("logs")
@click.option("--tail", "-t", is_flag=True, help="Follow (tail) log output")
@click.option("--level", "-l", default=None, help="Filter by level (ERROR, WARN, INFO, DEBUG)")
@click.option("--lines", "-n", default=50, type=int, help="Number of lines to show")
@click.option("--clear", "-c", "clear_logs", is_flag=True, help="Clear all logs")
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def logs_cmd(
    tail: bool,
    level: str | None,
    lines: int,
    clear_logs: bool,
    json_output: bool,
) -> None:
    """View, tail, or clear Nexus logs.

    Built-in logging happens to ~/.nexus/logs/nexus.log. Use this command
    to inspect or follow the log stream.

    Example:

        nexus logs                   # show last 50 lines

        nexus logs --tail            # follow in real-time

        nexus logs --level ERROR     # show only errors

        nexus logs --clear           # delete all logs
    """
    import json as _json
    import time

    log_dir = Path.home() / ".nexus" / "logs"
    log_file = log_dir / "nexus.log"

    if clear_logs:
        if log_file.exists():
            log_file.write_text("")
            click.echo(f"  \033[32m\u2713 Logs cleared\033[0m")
        else:
            click.echo(f"  \033[33mNo logs to clear\033[0m")
        return

    if not log_file.exists():
        click.echo(f"  \033[33mNo logs found at {log_file}\033[0m")
        return

    if json_output:
        entries = _parse_logs(log_file, level, lines)
        click.echo(_json.dumps(entries, indent=2))
        return

    if tail:
        _tail_logs(log_file, level)
        return

    _display_logs(log_file, level, lines)


def _parse_logs(log_file: Path, level: str | None, max_lines: int) -> list[dict]:
    """Parse log file into structured entries."""
    import json as _json

    entries = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = _json.loads(line)
                except _json.JSONDecodeError:
                    entry = {"message": line, "level": "INFO", "timestamp": ""}

                if level and entry.get("level", "").upper() != level.upper():
                    continue
                entries.append(entry)
    except Exception:
        pass

    return entries[-max_lines:]


def _display_logs(log_file: Path, level: str | None, max_lines: int) -> None:
    """Display recent log lines."""
    entries = _parse_logs(log_file, level, max_lines)

    if not entries:
        click.echo("  \033[33mNo log entries match the filter.\033[0m")
        return

    click.echo(f"  \033[1mNexus Logs\033[0m ({len(entries)} entries)")
    click.echo(f"  \033[90m{'=' * 50}\033[0m")

    for entry in entries:
        ts = entry.get("timestamp", "")[:19] if entry.get("timestamp") else ""
        lvl = entry.get("level", "INFO")
        msg = entry.get("message", "")

        lvl_color = {
            "ERROR": "\033[31m",
            "WARN": "\033[33m",
            "INFO": "\033[36m",
            "DEBUG": "\033[90m",
        }.get(lvl.upper(), "\033[0m")

        click.echo(f"  {ts} {lvl_color}{lvl:5s}\033[0m {msg[:200]}")
    click.echo()


def _tail_logs(log_file: Path, level: str | None) -> None:
    """Tail log file in real-time."""
    import time

    click.echo(f"  \033[36mTailing logs... Ctrl+C to stop\033[0m\n")
    try:
        with open(log_file) as f:
            # Seek to end
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        import json as _json
                        entry = _json.loads(line)
                    except Exception:
                        entry = {"message": line, "level": "INFO", "timestamp": ""}

                    if level and entry.get("level", "").upper() != level.upper():
                        continue

                    ts = entry.get("timestamp", "")[:19] if entry.get("timestamp") else ""
                    lvl = entry.get("level", "INFO")
                    msg = entry.get("message", "")
                    lvl_color = {
                        "ERROR": "\033[31m",
                        "WARN": "\033[33m",
                        "INFO": "\033[36m",
                        "DEBUG": "\033[90m",
                    }.get(lvl.upper(), "\033[0m")
                    click.echo(f"  {ts} {lvl_color}{lvl:5s}\033[0m {msg[:200]}")
                else:
                    time.sleep(0.1)
    except KeyboardInterrupt:
        click.echo("\n  \033[90mStopped\033[0m")


# =============================================================================
# completion — shell tab-completion
# =============================================================================


@cli.group("completion")
def completion():
    """Install shell tab-completion for Nexus.

    Supports bash, zsh, and fish shells.

    Example:

        nexus completion bash         # print bash completion script

        nexus completion zsh          # print zsh completion script

        nexus completion fish         # print fish completion script

        nexus completion install      # auto-install for current shell
    """
    pass


@completion.command("bash")
def completion_bash() -> None:
    """Print bash completion script."""
    import subprocess as _sp
    result = _sp.run(
        [_python(), "-m", "click", "completion", _script_name(), "--shell", "bash"],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        click.echo(result.stdout)
    else:
        _emit_fallback("bash")


@completion.command("zsh")
def completion_zsh() -> None:
    """Print zsh completion script."""
    import subprocess as _sp
    result = _sp.run(
        [_python(), "-m", "click", "completion", _script_name(), "--shell", "zsh"],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        click.echo(result.stdout)
    else:
        _emit_fallback("zsh")


@completion.command("fish")
def completion_fish() -> None:
    """Print fish completion script."""
    import subprocess as _sp
    result = _sp.run(
        [_python(), "-m", "click", "completion", _script_name(), "--shell", "fish"],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        click.echo(result.stdout)
    else:
        _emit_fallback("fish")


@completion.command("install")
@click.option("--shell", default=None, help="Shell to install for (bash, zsh, fish)")
def completion_install(shell: str | None) -> None:
    """Auto-detect shell and install tab-completion."""
    import os as _os
    import subprocess as _sp

    shell = shell or _os.environ.get("SHELL", "").split("/")[-1] or "bash"

    script = _sp.run(
        [_python(), "-m", "click", "completion", _script_name(), "--shell", shell],
        capture_output=True, text=True,
    ).stdout

    if not script.strip():
        click.echo(f"  \033[33mCould not generate completion for {shell}\033[0m")
        _emit_fallback(shell)
        return

    # Determine rc file
    home = str(Path.home())
    rc_files = {
        "bash": f"{home}/.bashrc",
        "zsh": f"{home}/.zshrc",
        "fish": f"{home}/.config/fish/completions/{_script_name()}.fish",
    }

    rc = rc_files.get(shell)
    if not rc:
        click.echo(f"  \033[33mUnknown shell: {shell}\033[0m")
        return

    # Ensure directory for fish
    if shell == "fish":
        Path(rc).parent.mkdir(parents=True, exist_ok=True)

    # Write
    try:
        if shell == "fish":
            Path(rc).write_text(script)
        else:
            with open(rc, "a") as f:
                f.write(f"\n# Nexus completion\n")
                f.write(f"eval '$({_script_name()} completion {shell})'\n")
        click.echo(f"  \033[32m\u2713 Completion installed for {shell}\033[0m")
        click.echo(f"  \033[90mRun: source {rc}\033[0m")
    except Exception as e:
        click.echo(f"  \033[31m\u2717 Install failed: {e}\033[0m")


def _python() -> str:
    """Return the Python executable path."""
    import sys as _sys
    return _sys.executable


def _script_name() -> str:
    """Return the CLI script name."""
    return "nexus"


def _emit_fallback(shell: str) -> None:
    """Emit a fallback completion message."""
    click.echo(f"# Nexus tab-completion for {shell}")
    click.echo(f"# Install: nexus completion install --shell {shell}")
    click.echo("# Or manually add to your shell rc file:")
    click.echo(f"#   eval $(nexus completion {shell})")


# =============================================================================
# reset — reset configuration
# =============================================================================


@cli.command("reset")
@click.option("--hard", is_flag=True, help="Full reset (delete all config, sessions, memory)")
@click.option("--keep-providers", is_flag=True, help="Keep provider configurations")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def reset_cmd(hard: bool, keep_providers: bool, force: bool) -> None:
    """Reset Nexus configuration to defaults.

    Example:

        nexus reset                  # reset config only

        nexus reset --hard           # delete everything

        nexus reset --keep-providers # keep API keys and providers

        nexus reset --force          # skip confirmation
    """
    import json as _json
    import shutil

    nexus_dir = Path.home() / ".nexus"

    if not nexus_dir.exists():
        click.echo("  \033[33mNo Nexus configuration found.\033[0m")
        return

    if not force:
        msg = (
            "\033[31mThis will reset your Nexus configuration.\033[0m\n"
            "  Continue?"
        )
        if hard:
            msg = (
                "\033[31mThis will DELETE all Nexus data (config, sessions, memory, logs).\033[0m\n"
                "  This cannot be undone. Continue?"
            )
        click.confirm(f"  {msg}", abort=True)

    if hard:
        try:
            shutil.rmtree(nexus_dir)
            click.echo(f"  \033[32m\u2713 Full reset complete. Deleted {nexus_dir}\033[0m")
            click.echo("  \033[90mRun 'nexus setup' to re-configure.\033[0m")
        except Exception as e:
            click.echo(f"  \033[31m\u2717 Reset failed: {e}\033[0m")
        return

    # Soft reset: reset config but keep providers if requested
    config_path = nexus_dir / "config.json"
    sessions_dir = nexus_dir / "sessions"
    memory_dir = nexus_dir / "memory"

    if config_path.exists():
        try:
            with open(config_path) as f:
                cfg = _json.load(f)

            if keep_providers:
                providers = cfg.get("providers", {})
                active = cfg.get("active_provider", "")
                cfg = {}
                cfg["providers"] = providers
                cfg["active_provider"] = active
            else:
                cfg = {}

            with open(config_path, "w") as f:
                _json.dump(cfg, f, indent=2)

            click.echo(f"  \033[32m\u2713 Configuration reset\033[0m")
            if keep_providers:
                click.echo(f"  \033[90mProviders preserved\033[0m")
        except Exception as e:
            click.echo(f"  \033[31m\u2717 Config reset failed: {e}\033[0m")

    # Clear sessions and memory (safe for soft reset too)
    for d in [sessions_dir, memory_dir]:
        if d.exists():
            try:
                shutil.rmtree(d)
                d.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass


# =============================================================================
# plugin — manage plugins
# =============================================================================


@cli.group("plugin")
def plugin():
    """Manage Nexus plugins.

    Plugins extend Nexus with custom middleware behaviour — tool hooks,
    message interceptors, session lifecycle callbacks.

    Example:

        nexus plugin list

        nexus plugin install ./my-plugin

        nexus plugin enable my-plugin

        nexus plugin disable my-plugin
    """
    pass


@plugin.command("list")
@click.option("--enabled/--all", "only_enabled", default=False, help="Show only enabled plugins")
def plugin_list(only_enabled: bool) -> None:
    """List installed plugins."""
    from nexus.plugins import get_plugin_manager
    pm = get_plugin_manager()
    plugins = pm.list_enabled() if only_enabled else pm.list_all()
    click.echo(f"\n  {_style('Plugins', 'cyan', bold=True)} ({len(plugins)})")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")
    for p in plugins:
        status = _style("● enabled", "green") if pm.is_enabled(p.name) else _style("○ disabled", "bright_black")
        click.echo(f"  {_style(p.name, bold=True):30s} {status}")
        if p.description:
            click.echo(f"  {_style(p.description, 'bright_black'):33s}")
    if not plugins:
        click.echo(f"  {_style('No plugins installed.', 'bright_black')}")
        click.echo(f"  {_style('Drop .py files in:', 'bright_black')} ~/.nexus/plugins/")


@plugin.command("enable")
@click.argument("name", required=True)
def plugin_enable(name: str) -> None:
    """Enable a plugin by name."""
    from nexus.plugins import get_plugin_manager
    pm = get_plugin_manager()
    if pm.enable(name):
        click.echo(f"  {_style('✔', 'green')} Plugin '{name}' enabled")
    else:
        click.echo(f"  {_style('✘', 'red')} Plugin '{name}' not found")


@plugin.command("disable")
@click.argument("name", required=True)
def plugin_disable(name: str) -> None:
    """Disable a plugin by name."""
    from nexus.plugins import get_plugin_manager
    pm = get_plugin_manager()
    if pm.disable(name):
        click.echo(f"  {_style('✔', 'green')} Plugin '{name}' disabled")
    else:
        click.echo(f"  {_style('✘', 'red')} Plugin '{name}' not found")


@plugin.command("install")
@click.argument("path", type=click.Path(exists=True))
def plugin_install(path: str) -> None:
    """Install a plugin from a directory or .py file."""
    from nexus.plugins import get_plugin_manager
    import shutil
    src = Path(path)
    dest_dir = Path.home() / ".nexus" / "plugins"
    dest_dir.mkdir(parents=True, exist_ok=True)

    if src.is_dir():
        dest = dest_dir / src.name
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        dest = dest_dir / src.name
        shutil.copy2(src, dest)

    click.echo(f"  {_style('✔', 'green')} Installed plugin: {_style(dest.name, bold=True)}")
    pm = get_plugin_manager()
    pm.discover()


@plugin.command("remove")
@click.argument("name", required=True)
def plugin_remove(name: str) -> None:
    """Remove a plugin by name."""
    import shutil
    plugin_dir = Path.home() / ".nexus" / "plugins"
    candidates = list(plugin_dir.glob(f"{name}*"))
    if not candidates:
        click.echo(f"  {_style('✘', 'red')} Plugin '{name}' not found")
        return
    for c in candidates:
        if c.is_dir():
            shutil.rmtree(c)
        else:
            c.unlink()
    click.echo(f"  {_style('✔', 'green')} Removed plugin: {_style(name, bold=True)}")


# =============================================================================
# scan — standalone codebase scanner
# =============================================================================


@cli.command("scan")
@click.argument("path", type=click.Path(), default=".", required=False)
@click.option("--output", "-o", type=click.Choice(["summary", "json", "tree"]), default="summary", help="Output format")
@click.option("--depth", default=3, type=int, help="Directory tree depth")
def scan_cmd(path: str, output: str, depth: int) -> None:
    """Scan a codebase and build project context.

    Analyses languages, frameworks, file counts, dependency files, and
    project structure. Useful for understanding a new codebase before
    working on it.

    Example:

        nexus scan                     # scan current directory

        nexus scan /path/to/project    # scan specific project

        nexus scan --output json       # machine-readable output

        nexus scan --output tree       # directory tree view
    """
    import json as _json
    from nexus.project import ProjectInitializer

    project_dir = Path(path).resolve()
    if not project_dir.exists():
        click.echo(f"  {_style('✘', 'red')} Directory not found: {project_dir}")
        return

    click.echo(f"\n  {_style('Scanning', 'cyan')} {_style(str(project_dir), 'white', bold=True)} ...")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")

    init = ProjectInitializer()
    info = init._scan_project(project_dir)

    if output == "json":
        click.echo(_json.dumps(info, indent=2))
        return

    if output == "tree":
        _print_directory_tree(project_dir, max_depth=depth)
        return

    # summary output
    click.echo(f"  {_style('Project type:', bold=True)}   {_style(info.get('type', 'unknown'), 'cyan')}")
    click.echo(f"  {_style('Languages:', bold=True)}     {_style(', '.join(info.get('languages', [])), 'white')}")
    click.echo(f"  {_style('Frameworks:', bold=True)}    {_style(', '.join(info.get('frameworks', [])), 'white')}")
    click.echo(f"  {_style('Files scanned:', bold=True)}  {info.get('file_count', 0)}")

    # Show key config files
    key_files = _find_key_files(project_dir)
    if key_files:
        click.echo()
        click.echo(f"  {_style('Key files:', bold=True)}")
        for kf in key_files:
            click.echo(f"    {_style('📄', 'bright_black')} {kf}")

    if info.get("warnings"):
        click.echo()
        for w in info["warnings"]:
            click.echo(f"  {_style('⚠', 'yellow')} {w}")

    click.echo()


def _print_directory_tree(path: Path, prefix: str = "", max_depth: int = 3, _depth: int = 0) -> None:
    """Print a directory tree."""
    if _depth > max_depth:
        return
    try:
        entries = sorted(path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except PermissionError:
        return

    for i, entry in enumerate(entries):
        if entry.name.startswith(".") or entry.name.startswith("__"):
            continue
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        click.echo(f"  {prefix}{connector}{_style(entry.name, 'cyan' if entry.is_dir() else 'white')}")
        if entry.is_dir():
            extension = "    " if is_last else "│   "
            _print_directory_tree(entry, prefix + extension, max_depth, _depth + 1)


def _find_key_files(path: Path) -> list[str]:
    """Find important project configuration files."""
    key_names = [
        "README.md", "README", "package.json", "pyproject.toml", "Cargo.toml",
        "go.mod", "Gemfile", "Makefile", "CMakeLists.txt", "composer.json",
        "pom.xml", "build.gradle", "mix.exs", "stack.yaml", "pubspec.yaml",
        "deno.json", "deno.jsonc", "bun.lockb", "Dockerfile", "docker-compose.yml",
        ".env.example", "CONTRIBUTING.md", "CHANGELOG.md", "LICENSE",
        "tsconfig.json", "webpack.config.js", "vite.config.ts", "tailwind.config.js",
        "next.config.js", "astro.config.mjs", "svelte.config.js",
    ]
    found = []
    for name in key_names:
        candidate = path / name
        if candidate.exists():
            found.append(name)
    return found


# =============================================================================
# export / import / backup — data portability
# =============================================================================


@cli.command("export")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output file path")
@click.option("--include", multiple=True, default=["config", "sessions", "memory", "skills", "plugins", "logs"],
              help="Components to export")
def export_cmd(output: str | None, include: list[str]) -> None:
    """Export Nexus data (config, sessions, memory, skills, plugins).

    Creates a portable .zip archive of your Nexus configuration and data.

    Example:

        nexus export

        nexus export --output ~/nexus-backup.zip

        nexus export --include config --include sessions
    """
    import json as _json
    import shutil
    import tempfile
    import zipfile
    from datetime import datetime

    nexus_dir = Path.home() / ".nexus"
    if not nexus_dir.exists():
        click.echo(f"  {_style('✘', 'red')} No Nexus data found at {nexus_dir}")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(output or f"nexus_export_{timestamp}.zip")

    click.echo(f"\n  {_style('Exporting Nexus data', 'cyan')} ...")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        manifest: dict[str, list[str]] = {"exported_at": timestamp, "components": []}

        for component in include:
            component = component.lower()
            if component == "config":
                src = nexus_dir / "config.json"
                if src.exists():
                    shutil.copy2(src, tmp / "config.json")
                    manifest["components"].append("config")
                    click.echo(f"    {_style('✔', 'green')} config.json")

            elif component == "sessions":
                src = nexus_dir / "sessions"
                if src.exists():
                    shutil.copytree(src, tmp / "sessions", dirs_exist_ok=True)
                    manifest["components"].append("sessions")
                    click.echo(f"    {_style('✔', 'green')} sessions/ ({len(list(src.glob('*.json')))} files)")

            elif component == "memory":
                src = nexus_dir / "memory"
                if src.exists():
                    shutil.copytree(src, tmp / "memory", dirs_exist_ok=True)
                    manifest["components"].append("memory")
                    click.echo(f"    {_style('✔', 'green')} memory/")

            elif component == "skills":
                src = nexus_dir / "skills"
                if src.exists():
                    shutil.copytree(src, tmp / "skills", dirs_exist_ok=True)
                    manifest["components"].append("skills")
                    click.echo(f"    {_style('✔', 'green')} skills/")

            elif component == "plugins":
                src = nexus_dir / "plugins"
                if src.exists():
                    shutil.copytree(src, tmp / "plugins", dirs_exist_ok=True)
                    manifest["components"].append("plugins")
                    click.echo(f"    {_style('✔', 'green')} plugins/")

            elif component == "logs":
                src = nexus_dir / "logs"
                if src.exists():
                    shutil.copytree(src, tmp / "logs", dirs_exist_ok=True)
                    manifest["components"].append("logs")
                    click.echo(f"    {_style('✔', 'green')} logs/")

        # Write manifest
        (tmp / "manifest.json").write_text(_json.dumps(manifest, indent=2))

        # Create zip
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in tmp.rglob("*"):
                if f.is_file():
                    zf.write(f, f.relative_to(tmp))

    click.echo()
    click.echo(f"  {_style('✔', 'green')} Exported to: {_style(str(output_path), 'cyan', bold=True)}")
    click.echo(f"  {_style(f'Size: {output_path.stat().st_size / 1024:.1f} KB', 'bright_black')}")


@cli.command("import")
@click.argument("file", type=click.Path(exists=True))
@click.option("--force", is_flag=True, help="Overwrite existing data")
def import_cmd(file: str, force: bool) -> None:
    """Import Nexus data from an export archive.

    Example:

        nexus import nexus_export_20250101_120000.zip

        nexus import ~/Downloads/nexus-backup.zip --force
    """
    import json as _json
    import shutil
    import tempfile
    import zipfile

    import_path = Path(file)
    if import_path.suffix not in (".zip",):
        click.echo(f"  {_style('✘', 'red')} Expected a .zip file")
        return

    nexus_dir = Path.home() / ".nexus"
    click.echo(f"\n  {_style('Importing from', 'cyan')} {_style(str(import_path), 'white', bold=True)}")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with zipfile.ZipFile(import_path, "r") as zf:
            zf.extractall(tmp)

        manifest_file = tmp / "manifest.json"
        if not manifest_file.exists():
            click.echo(f"  {_style('✘', 'red')} Invalid export: no manifest.json")
            return

        manifest = _json.loads(manifest_file.read_text())
        click.echo(f"  Found components: {_style(', '.join(manifest.get('components', [])), 'white')}")

        for component in manifest.get("components", []):
            src = tmp / component
            if not src.exists():
                continue

            if component == "config":
                dest = nexus_dir / "config.json"
                if dest.exists() and not force:
                    click.echo(f"  {_style('⚠', 'yellow')} config.json exists (use --force to overwrite)")
                    continue
                shutil.copy2(src, dest)
                click.echo(f"    {_style('✔', 'green')} config.json")

            elif component == "sessions":
                dest = nexus_dir / "sessions"
                if dest.exists() and not force:
                    click.echo(f"  {_style('⚠', 'yellow')} sessions/ exists (use --force to overwrite)")
                    continue
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                click.echo(f"    {_style('✔', 'green')} sessions/")

            elif component == "memory":
                dest = nexus_dir / "memory"
                if dest.exists() and not force:
                    click.echo(f"  {_style('⚠', 'yellow')} memory/ exists (use --force to overwrite)")
                    continue
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                click.echo(f"    {_style('✔', 'green')} memory/")

            elif component == "skills":
                dest = nexus_dir / "skills"
                if dest.exists() and not force:
                    click.echo(f"  {_style('⚠', 'yellow')} skills/ exists (use --force to overwrite)")
                    continue
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                click.echo(f"    {_style('✔', 'green')} skills/")

            elif component == "plugins":
                dest = nexus_dir / "plugins"
                if dest.exists() and not force:
                    click.echo(f"  {_style('⚠', 'yellow')} plugins/ exists (use --force to overwrite)")
                    continue
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                click.echo(f"    {_style('✔', 'green')} plugins/")

            elif component == "logs":
                dest = nexus_dir / "logs"
                dest.mkdir(parents=True, exist_ok=True)
                for f in src.iterdir():
                    shutil.copy2(f, dest / f.name)
                click.echo(f"    {_style('✔', 'green')} logs/")

    click.echo(f"\n  {_style('✔', 'green')} Import complete")


@cli.command("backup")
@click.option("--output", "-o", type=click.Path(), default=None, help="Output directory")
def backup_cmd(output: str | None) -> None:
    """Quick backup of Nexus configuration and data.

    Equivalent to 'nexus export' with a default name and all components.

    Example:

        nexus backup

        nexus backup --output ~/my-backups/
    """
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output or ".").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    default_path = str(out_dir / f"nexus_backup_{timestamp}.zip")

    ctx = click.get_current_context()
    ctx.invoke(export_cmd, output=default_path, include=["config", "sessions", "memory", "skills", "plugins", "logs"])


# =============================================================================
# safety — safety mode & rules management
# =============================================================================


@cli.group("safety")
def safety():
    """Manage safety modes and rules.

    Nexus has three safety modes:

        off    — No restrictions (dangerous, use with care)
        normal — Standard permissions (ask before writing files, running commands)
        strict — Maximum protection (read-only by default, explicit approval needed)

    Example:

        nexus safety status

        nexus safety mode strict

        nexus safety mode normal
    """
    pass


@safety.command("status")
def safety_status() -> None:
    """Show current safety mode and rules."""
    from nexus.safety import get_safety_engine
    engine = get_safety_engine()
    mode = engine.get_mode().value if hasattr(engine, "get_mode") else "unknown"
    violations = getattr(engine, "_violations", [])
    rules = getattr(engine, "rules", {})
    rules_list = list(rules.values()) if isinstance(rules, dict) else list(rules)

    click.echo(f"\n  {_style('Safety Status', 'cyan', bold=True)}")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")
    mode_styles = {"read_only": ("yellow", "● READ_ONLY"), "user_review": ("green", "● USER_REVIEW"),
                   "strict": ("red", "● STRICT"), "unrestricted": ("red", "● UNRESTRICTED"),
                   "sensitive": ("yellow", "● SENSITIVE"), "auto_git": ("green", "● AUTO_GIT"),
                   "sandbox": ("cyan", "● SANDBOX")}
    fg, label = mode_styles.get(mode, ("white", mode))
    click.echo(f"  {_style('Mode:', bold=True)}       {_style(label, fg=fg)}")
    click.echo(f"  {_style('Rules:', bold=True)}      {len(rules_list)}")
    click.echo(f"  {_style('Violations:', bold=True)} {len(violations)}")

    if rules_list:
        click.echo()
        click.echo(f"  {_style('Active rules:', 'bright_black', bold=True)}")
        for r in rules_list[:10]:
            rid = getattr(r, "id", "?")
            rname = getattr(r, "name", "?")
            click.echo(f"    {_style('•', 'bright_black')} {_style(rname, 'white')}  {_style(f'({rid})', 'bright_black')}")
    click.echo()


@safety.command("mode")
@click.argument("mode", type=click.Choice(["read_only", "user_review", "strict", "unrestricted", "sensitive", "auto_git", "sandbox"]))
def safety_mode(mode: str) -> None:
    """Set safety mode (read_only / user_review / strict / unrestricted / sensitive / auto_git / sandbox)."""
    from nexus.safety import SafetyMode, get_safety_engine
    engine = get_safety_engine()
    mode_map = {
        "read_only": SafetyMode.READ_ONLY, "sensitive": SafetyMode.SENSITIVE_WRITE,
        "auto_git": SafetyMode.AUTO_GIT, "sandbox": SafetyMode.LOCAL_SANDBOX,
        "user_review": SafetyMode.USER_REVIEW, "unrestricted": SafetyMode.UNRESTRICTED,
        "strict": SafetyMode.STRICT,
    }
    if mode not in mode_map:
        click.echo(f"  {_style('✘', 'red')} Invalid mode. Choose: {', '.join(mode_map.keys())}")
        return
    engine.set_mode(mode_map[mode])
    click.echo(f"  {_style('✔', 'green')} Safety mode set to: {_style(mode, 'cyan', bold=True)}")


@safety.command("rules")
@click.option("--category", help="Filter by category")
def safety_rules(category: str | None) -> None:
    """List all safety rules."""
    from nexus.safety import get_safety_engine
    engine = get_safety_engine()
    all_rules = getattr(engine, "rules", {})
    rules = list(all_rules.values()) if isinstance(all_rules, dict) else list(all_rules)
    if category:
        rules = [r for r in rules if hasattr(r, "category") and r.category and r.category.value == category]
    click.echo(f"\n  {_style('Safety Rules', 'cyan', bold=True)} ({len(rules)})")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")
    for r in rules:
        rid = getattr(r, "id", "?")
        rname = getattr(r, "name", "?")
        rcat = getattr(r, "category", "")
        rcat_str = f" [{_style(rcat.value if hasattr(rcat, 'value') else str(rcat), 'yellow')}]" if rcat else ""
        click.echo(f"  {_style(rname, bold=True):35s} {_style(rid, 'bright_black')}{rcat_str}")
    click.echo()


# =============================================================================
# agents — multi-agent management
# =============================================================================


@cli.group("agents")
def agents():
    """Manage multi-agent teams.

    Nexus supports spawning sub-agents to work on tasks in parallel,
    forming teams with specialised roles.

    Example:

        nexus agents list

        nexus agents spawn "Fix the login bug"

        nexus agents kill <agent-id>
    """
    pass


@agents.command("list")
def agents_list() -> None:
    """List all active agents."""
    from nexus.agents import MultiAgentTeam
    team = MultiAgentTeam()
    agent_list = getattr(team, "_agents", []) if hasattr(team, "_agents") else []

    click.echo(f"\n  {_style('Active Agents', 'cyan', bold=True)} ({len(agent_list)})")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")
    for a in agent_list:
        name = getattr(a, "name", "?")
        role = getattr(a, "role", "?")
        status = getattr(a, "status", "?")
        click.echo(f"  {_style(name, bold=True):25s} {_style(str(role.value if hasattr(role, 'value') else role), 'cyan'):20s} {_style(str(status.value if hasattr(status, 'value') else status), 'bright_black')}")
    if not agent_list:
        click.echo(f"  {_style('No active agents.', 'bright_black')}")
        click.echo(f"  {_style('Use nexus agents spawn <task>', 'bright_black')} to create one.")
    click.echo()


@agents.command("spawn")
@click.argument("task", required=True)
@click.option("--name", default=None, help="Agent name")
@click.option("--role", default="coder", type=click.Choice(["lead", "planner", "coder", "reviewer", "tester", "researcher"]), help="Agent role")
def agents_spawn(task: str, name: str | None, role: str) -> None:
    """Spawn a new agent for a specific task."""
    from nexus.agents import AgentRole, MultiAgentTeam
    from nexus.config import load_config

    config = load_config()
    team = MultiAgentTeam(provider_manager=None)

    role_map = {
        "lead": AgentRole.LEAD,
        "planner": AgentRole.PLANNER,
        "coder": AgentRole.CODER,
        "reviewer": AgentRole.REVIEWER,
        "tester": AgentRole.TESTER,
        "researcher": AgentRole.RESEARCHER,
    }
    try:
        agent = team.spawn(
            task=task,
            role=role_map[role],
            name=name,
        )
        click.echo(f"  {_style('✔', 'green')} Spawned agent: {_style(agent.name, bold=True)}")
        click.echo(f"  {_style('ID:', 'bright_black')}     {agent.id}")
        click.echo(f"  {_style('Role:', 'bright_black')}   {role}")
        click.echo(f"  {_style('Task:', 'bright_black')}   {task[:60]}")
    except Exception as e:
        click.echo(f"  {_style('✘', 'red')} Failed to spawn agent: {e}")


@agents.command("kill")
@click.argument("agent_id", required=True)
def agents_kill(agent_id: str) -> None:
    """Kill a running agent by ID."""
    from nexus.agents import MultiAgentTeam
    team = MultiAgentTeam()
    if hasattr(team, "remove_agent"):
        team.remove_agent(agent_id)
        click.echo(f"  {_style('✔', 'green')} Agent {_style(agent_id, bold=True)} killed")
    else:
        click.echo(f"  {_style('✘', 'red')} Agent removal not supported")


@cli.command("team")
@click.argument("task", required=True)
@click.option("--members", default=3, type=int, help="Number of team members")
def team_cmd(task: str, members: int) -> None:
    """Assemble a team of agents for a complex task.

    Creates a multi-agent team with a lead and specialised workers
    to tackle a complex problem collaboratively.

    Example:

        nexus team \"Build a REST API\"

        nexus team \"Refactor the codebase\" --members 5
    """
    from nexus.agents import AgentRole, MultiAgentTeam
    from nexus.config import load_config

    config = load_config()

    click.echo(f"\n  {_style('Assembling team for:', 'cyan')} {_style(task, 'white', bold=True)}")
    click.echo(f"  {_style('─' * 50, 'bright_black')}")

    team = MultiAgentTeam(lead_name="nexus-lead", provider_manager=None)

    roles = [AgentRole.PLANNER, AgentRole.RESEARCHER, AgentRole.REVIEWER]
    spawned = []
    for i in range(min(members - 1, len(roles))):
        try:
            agent = team.spawn(
                task=task,
                role=roles[i],
                name=f"{roles[i].value}-{i+1}",
            )
            spawned.append(agent)
            click.echo(f"  {_style('✔', 'green')} {_style(roles[i].value.capitalize(), 'cyan'):15s} {_style(agent.name, bold=True)}")
        except Exception:
            pass

    if spawned:
        click.echo()
        click.echo(f"  {_style(f'Team assembled with {len(spawned) + 1} members', 'green', bold=True)}")
        click.echo(f"  {_style('Use /agents in REPL to monitor them.', 'bright_black')}")
    else:
        click.echo(f"  {_style('⚠', 'yellow')} Could not spawn team members")
        click.echo(f"  {_style('Make sure a provider is configured:', 'bright_black')}  nexus setup")


# Initialize providers from config
def initialize_providers(config) -> None:
    """Initialize providers from configuration."""
    from nexus.providers import get_manager

    manager = get_manager()
    for _name, cfg in config.providers.items():
        manager.add_provider(cfg)

    if config.active_provider in config.providers:
        manager.set_active(config.active_provider)


def main():
    """Main entry point."""
    # Fast path for version and help to keep it 'clean'
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "--help"):
        cli(obj={})
        return

    from nexus.config import load_config

    # Load config and initialize
    config = load_config()
    config.ensure_dirs()

    # Check for providers
    if not config.providers:
        from nexus.doctor import run_doctor

        # If we are in the main CLI (not tui/voice/repl), assume interactive unless --non-interactive
        is_interactive = sys.stdin.isatty() and "--non-interactive" not in sys.argv
        run_doctor(interactive=is_interactive)
        # Reload config after setup
        config = load_config()

    initialize_providers(config)

    # Run CLI
    cli(obj={"config": config})


if __name__ == "__main__":
    main()
