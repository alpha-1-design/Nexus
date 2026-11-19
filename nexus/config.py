"""Configuration management for Nexus.

Includes the global ``.env`` machinery: Nexus loads ``~/.nexus/.env`` and any
``.env`` in the current working directory with a tiny stdlib-only parser (no
python-dotenv dependency — Termux-friendly). Credentials therefore live in ONE
global place; plaintext keys are never written into ``config.json`` anymore
(env-sourced keys serialize as null, legacy config.json keys still read).
"""

import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / ".nexus"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_ENV_FILE = DEFAULT_CONFIG_DIR / ".env"
DEFAULT_ENV_EXAMPLE_FILE = DEFAULT_CONFIG_DIR / ".env.example"
DEFAULT_MEMORY_DIR = DEFAULT_CONFIG_DIR / "memory"
DEFAULT_SKILLS_DIR = DEFAULT_CONFIG_DIR / "skills"
DEFAULT_PLUGINS_DIR = DEFAULT_CONFIG_DIR / "plugins"
DEFAULT_MCP_DIR = DEFAULT_CONFIG_DIR / "mcp_servers"

_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class ProviderConfig:
    """Configuration for an AI provider."""

    name: str
    provider_type: str  # openai, anthropic, google, ollama, groq, deepseek, etc.
    api_key: str | None = None
    base_url: str | None = None
    model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 120
    enabled: bool = True
    # True when the key came from the environment / global .env: it stays
    # usable in memory but is NEVER serialized back into config.json.
    env_sourced: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider_type": self.provider_type,
            "api_key": None if self.env_sourced else self.api_key,
            "base_url": self.base_url,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "timeout": self.timeout,
            "enabled": self.enabled,
        }


@dataclass
class NexusConfig:
    """Main configuration for Nexus."""

    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    active_provider: str = "openai"
    planner_model: str | None = None
    worker_model: str | None = None
    config_dir: Path = field(default_factory=lambda: DEFAULT_CONFIG_DIR)
    memory_dir: Path = field(default_factory=lambda: DEFAULT_MEMORY_DIR)
    skills_dir: Path = field(default_factory=lambda: DEFAULT_SKILLS_DIR)
    plugins_dir: Path = field(default_factory=lambda: DEFAULT_PLUGINS_DIR)
    mcp_dir: Path = field(default_factory=lambda: DEFAULT_MCP_DIR)
    log_level: str = "INFO"
    sandbox_mode: str = "off"  # off, non-main, all
    tool_profile: str = "coding"  # minimal, default, coding, all

    # Free-tier rotation: when a provider rate-limits, rotate to the next
    # free provider with a key (see providers/manager.py).
    free_rotation: bool = True

    # Search providers
    search_provider: str = "exa"
    tavily_api_key: str | None = None
    brave_api_key: str | None = None
    exa_api_key: str | None = None

    # Personalisation
    user_name: str = ""

    # First-run flag (used by the onboarding wizard)
    first_run: bool = False

    # Termux-specific
    termux_mode: bool = False
    clipboard_tool: str = "termux-clipboard-set"

    @staticmethod
    def _sourced_from_env(env_name: str, value: str | None) -> bool:
        """True when ``value`` simply mirrors the environment (don't persist)."""
        return bool(value) and os.environ.get(env_name) == value

    def ensure_dirs(self) -> None:
        """Ensure all required directories exist."""
        for d in [self.memory_dir, self.skills_dir, self.plugins_dir, self.mcp_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "providers": {k: v.to_dict() for k, v in self.providers.items()},
            "active_provider": self.active_provider,
            "planner_model": self.planner_model,
            "worker_model": self.worker_model,
            "memory_dir": str(self.memory_dir),
            "skills_dir": str(self.skills_dir),
            "plugins_dir": str(self.plugins_dir),
            "mcp_dir": str(self.mcp_dir),
            "log_level": self.log_level,
            "sandbox_mode": self.sandbox_mode,
            "tool_profile": self.tool_profile,
            "free_rotation": self.free_rotation,
            "search_provider": self.search_provider,
            # Search keys sourced from the environment/.env are not persisted.
            "tavily_api_key": None if self._sourced_from_env("TAVILY_API_KEY", self.tavily_api_key) else self.tavily_api_key,
            "brave_api_key": None if self._sourced_from_env("BRAVE_API_KEY", self.brave_api_key) else self.brave_api_key,
            "exa_api_key": None if self._sourced_from_env("EXA_API_KEY", self.exa_api_key) else self.exa_api_key,
            "user_name": self.user_name,
            "termux_mode": self.termux_mode,
            "clipboard_tool": self.clipboard_tool,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NexusConfig":
        config = cls()
        if "providers" in data:
            known = {f.name for f in fields(ProviderConfig)}
            config.providers = {
                k: ProviderConfig(**{kk: vv for kk, vv in v.items() if kk in known})
                for k, v in data["providers"].items()
            }
        config.active_provider = data.get("active_provider", "openai")
        config.planner_model = data.get("planner_model")
        config.worker_model = data.get("worker_model")
        if "memory_dir" in data:
            config.memory_dir = Path(data["memory_dir"])
        if "skills_dir" in data:
            config.skills_dir = Path(data["skills_dir"])
        if "plugins_dir" in data:
            config.plugins_dir = Path(data["plugins_dir"])
        if "mcp_dir" in data:
            config.mcp_dir = Path(data["mcp_dir"])
        config.log_level = data.get("log_level", "INFO")
        config.sandbox_mode = data.get("sandbox_mode", "off")
        config.tool_profile = data.get("tool_profile", "coding")
        config.free_rotation = data.get("free_rotation", True)
        config.search_provider = data.get("search_provider", "exa")
        config.user_name = data.get("user_name", "")
        config.tavily_api_key = data.get("tavily_api_key")
        config.brave_api_key = data.get("brave_api_key")
        config.exa_api_key = data.get("exa_api_key")
        config.first_run = data.get("first_run", False)
        config.termux_mode = data.get("termux_mode", False)
        config.clipboard_tool = data.get("clipboard_tool", "termux-clipboard-set")
        return config


# =============================================================================
# Global .env support (stdlib-only, Termux-friendly)
# =============================================================================


def parse_env_text(text: str) -> tuple[dict[str, str], int]:
    """Parse dotenv-format text into ``({key: value}, malformed_line_count)``.

    Supports ``KEY=VALUE``, ``export KEY=VALUE``, single/double quotes, blank
    lines, ``#`` comments (whole-line and trailing `` #`` on unquoted values)
    and whitespace tolerance. Malformed lines are skipped silently but counted.
    """
    parsed: dict[str, str] = {}
    malformed = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export ") or line.startswith("export\t"):
            line = line[6:].lstrip()
        if "=" not in line:
            malformed += 1
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or not _ENV_KEY_RE.fullmatch(key):
            malformed += 1
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
            if quote == '"':
                value = (
                    value.replace("\\\\", "\\")
                    .replace('\\"', '"')
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                )
            # single quotes: literal, no escape processing
        else:
            hash_idx = value.find(" #")
            if hash_idx != -1:
                value = value[:hash_idx].rstrip()
        parsed[key] = value
    return parsed, malformed


def parse_env_file(path: Path) -> tuple[dict[str, str], int]:
    """Read and parse one .env file. Unreadable files parse as empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, 0
    return parse_env_text(text)


def _apply_env_vars(
    parsed: dict[str, str],
    environ: dict[str, str],
    merged: dict[str, str],
) -> None:
    """Set vars into ``environ`` unless already set by the real environment or
    an earlier .env file (later files never override earlier sources)."""
    for key, value in parsed.items():
        if key in environ or key in merged:
            continue
        environ[key] = value
        merged[key] = value


def _chmod_600(path: Path) -> None:
    """Best-effort chmod 600 — ignore failures on filesystems without chmod
    (e.g. FAT/NTFS on some Android setups)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure_env_example(path: Path | None = None) -> Path:
    """Create ``~/.nexus/.env.example`` (never a real .env) if missing."""
    target = path or DEFAULT_ENV_EXAMPLE_FILE
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ENV_EXAMPLE_TEMPLATE, encoding="utf-8")
    return target


def load_env(
    env_file: Path | None = None,
    cwd_file: Path | None = None,
    environ: dict[str, str] | None = None,
    create_example: bool = True,
) -> dict[str, str]:
    """Load Nexus .env files into the process environment.

    Order: ``~/.nexus/.env`` first, then a ``.env`` in the current working
    directory. Variables already set by the real environment or by an earlier
    file are NEVER overridden. Returns the mapping of variables that were
    actually applied. When the home .env is missing, ``.env.example`` is
    created as a template (never a real .env). The real .env gets chmod 600.
    """
    environ = os.environ if environ is None else environ
    merged: dict[str, str] = {}

    home_file = env_file if env_file is not None else DEFAULT_ENV_FILE
    if create_example and not home_file.exists():
        ensure_env_example(home_file.parent / ".env.example")

    if home_file.exists():
        parsed, _ = parse_env_file(home_file)
        _apply_env_vars(parsed, environ, merged)
        _chmod_600(home_file)

    cwd_path = cwd_file if cwd_file is not None else Path.cwd() / ".env"
    if cwd_path.exists() and cwd_path.resolve() != home_file.resolve():
        parsed, _ = parse_env_file(cwd_path)
        _apply_env_vars(parsed, environ, merged)

    return merged


def write_env_key(key: str, value: str, env_path: Path | None = None) -> Path:
    """Create or update one ``KEY=VALUE`` entry in the global ``~/.nexus/.env``.

    Existing lines are preserved; an existing entry for ``key`` is replaced in
    place, otherwise appended. The file is chmod 600 (best-effort). The value
    is never logged or printed.
    """
    path = env_path or DEFAULT_ENV_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"{key}="
    replacement = f"{key}={value}"
    lines: list[str] = []
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    new_lines: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("#") and stripped.startswith(prefix):
            new_lines.append(replacement)
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append(replacement)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    _chmod_600(path)
    return path


ENV_EXAMPLE_TEMPLATE = """\
# Nexus environment template — copy to ~/.nexus/.env and uncomment what you use.
#   cp ~/.nexus/.env.example ~/.nexus/.env && chmod 600 ~/.nexus/.env
# Nexus loads ~/.nexus/.env at startup, then any .env in the current directory.
# Variables already set in the real environment always win. Keep this file
# private — never commit a real .env.
#
# ============================ FREE TIER (start here) =========================
# Grab ONE of these keys and Nexus rotates across every free provider you
# configure when one rate-limits (recommended: Groq or OpenRouter).

# Groq — near-instant Llama inference, generous free tier.
#   Key: https://console.groq.com/keys
# GROQ_API_KEY=

# OpenRouter — hundreds of models, many :free (free model ids rotate; the
#   "openrouter/free" router model always works). Key: https://openrouter.ai/keys
# OPENROUTER_API_KEY=

# Google AI Studio (Gemini) — strong free tier. Key: https://aistudio.google.com/app/apikey
# GEMINI_API_KEY=

# Cerebras — the fastest inference available, free API key.
#   Key: https://cloud.cerebras.ai
# CEREBRAS_API_KEY=

# Mistral La Plateforme — free experiment tier (small/codestral models).
#   Key: https://console.mistral.ai/api-keys/
# MISTRAL_API_KEY=

# GitHub Models — free GitHub-hosted models via a personal access token.
#   Token: https://github.com/settings/tokens
# GITHUB_TOKEN=

# HuggingFace Router — one token routes to all HF Inference Providers.
#   Token: https://huggingface.co/settings/tokens
# HF_TOKEN=

# Cloudflare Workers AI — 10k free neurons/day. Needs BOTH vars; the account
#   id is part of the endpoint URL. Tokens: https://dash.cloudflare.com/profile/api-tokens
# CLOUDFLARE_API_TOKEN=
# CLOUDFLARE_ACCOUNT_ID=

# Zhipu GLM open platform — glm-4-flash is free.
#   Key: https://open.bigmodel.cn
# ZHIPUAI_API_KEY=

# OpenCode Zen — zero-cost coding models. https://opencode.ai/zen
# OPENCODE_ZEN_API_KEY=
# OPENCODE_GO_API_KEY=

# Ollama (local, offline, free) — set OLLAMA_HOST to enable, e.g. http://localhost:11434
# OLLAMA_HOST=

# ============================== PAID PROVIDERS ===============================

# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
# DEEPSEEK_API_KEY=

# ============================ SEARCH PROVIDERS ===============================

# Tavily — https://tavily.com
# TAVILY_API_KEY=
# Brave Search — https://brave.com/search/api/
# BRAVE_API_KEY=
# Exa — https://exa.ai
# EXA_API_KEY=
"""


# =============================================================================
# Provider auto-detection from the environment / global .env
# =============================================================================


def _detect_env_providers(environ: dict[str, str] | None = None) -> dict[str, ProviderConfig]:
    """Build ProviderConfig entries for every provider with credentials in the
    environment (global .env included). Detected keys are marked env_sourced so
    they are never persisted back into config.json."""
    env = os.environ if environ is None else environ
    detected: dict[str, ProviderConfig] = {}

    def add(
        name: str,
        ptype: str,
        key: str,
        model: str,
        model_env: str,
        base_url: str | None = None,
    ) -> None:
        detected[name] = ProviderConfig(
            name=name,
            provider_type=ptype,
            api_key=key,
            base_url=base_url,
            model=env.get(model_env, model),
            env_sourced=True,
        )

    if env.get("OPENAI_API_KEY"):
        add("openai", "openai", env["OPENAI_API_KEY"], "gpt-4o", "OPENAI_MODEL")
    if env.get("ANTHROPIC_API_KEY"):
        add("anthropic", "anthropic", env["ANTHROPIC_API_KEY"], "claude-sonnet-4-20250514", "ANTHROPIC_MODEL")
    gemini_key = env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY")
    if gemini_key:
        add("google", "google", gemini_key, "gemini-2.0-flash", "GOOGLE_MODEL")
    if env.get("GROQ_API_KEY"):
        add("groq", "groq", env["GROQ_API_KEY"], "llama-3.3-70b-versatile", "GROQ_MODEL")
    if env.get("DEEPSEEK_API_KEY"):
        add("deepseek", "deepseek", env["DEEPSEEK_API_KEY"], "deepseek-chat", "DEEPSEEK_MODEL")
    if env.get("MISTRAL_API_KEY"):
        add("mistral", "mistral", env["MISTRAL_API_KEY"], "mistral-small-latest", "MISTRAL_MODEL")
    if env.get("OPENROUTER_API_KEY"):
        add("openrouter", "openrouter", env["OPENROUTER_API_KEY"], "openrouter/free", "OPENROUTER_MODEL")
    if env.get("CEREBRAS_API_KEY"):
        add("cerebras", "cerebras", env["CEREBRAS_API_KEY"], "llama-3.3-70b", "CEREBRAS_MODEL")
    if env.get("GITHUB_TOKEN"):
        add("github-models", "github-models", env["GITHUB_TOKEN"], "openai/gpt-4o-mini", "GITHUB_MODELS_MODEL")
    if env.get("HF_TOKEN"):
        add("huggingface", "huggingface", env["HF_TOKEN"], "meta-llama/Llama-3.3-70B-Instruct", "HF_MODEL")
    if env.get("CLOUDFLARE_API_TOKEN") and env.get("CLOUDFLARE_ACCOUNT_ID"):
        add(
            "cloudflare-workers-ai",
            "cloudflare-workers-ai",
            env["CLOUDFLARE_API_TOKEN"],
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "CLOUDFLARE_MODEL",
            base_url=f"https://api.cloudflare.com/client/v4/accounts/{env['CLOUDFLARE_ACCOUNT_ID']}/ai/v1",
        )
    if env.get("ZHIPUAI_API_KEY"):
        add("zhipu", "zhipu", env["ZHIPUAI_API_KEY"], "glm-4-flash", "ZHIPUAI_MODEL")
    if env.get("OPENCODE_ZEN_API_KEY"):
        add("opencode-zen", "opencode-zen", env["OPENCODE_ZEN_API_KEY"], "minimax-m2.5-free", "OPENCODE_ZEN_MODEL", base_url="https://opencode.ai/zen/v1")
    if env.get("OPENCODE_GO_API_KEY"):
        add("opencode-go", "opencode-go", env["OPENCODE_GO_API_KEY"], "kimi-k2.5", "OPENCODE_GO_MODEL", base_url="https://opencode.ai/zen/go/v1")
    if env.get("OLLAMA_HOST"):
        detected["ollama"] = ProviderConfig(
            name="ollama",
            provider_type="ollama",
            base_url=env.get("OLLAMA_HOST", "http://localhost:11434"),
            model=env.get("OLLAMA_MODEL", "llama3"),
            env_sourced=True,
        )
    return detected


def _apply_env_defaults(config: NexusConfig) -> None:
    """Fold env/.env credentials into an existing config WITHOUT overriding
    anything stored in config.json (legacy fallback), and fill search keys
    from the environment when unset."""
    for name, prov in _detect_env_providers().items():
        if name not in config.providers:
            config.providers[name] = prov
    if config.providers and config.active_provider not in config.providers:
        config.active_provider = next(iter(config.providers))
    for attr, env_name in (
        ("tavily_api_key", "TAVILY_API_KEY"),
        ("brave_api_key", "BRAVE_API_KEY"),
        ("exa_api_key", "EXA_API_KEY"),
    ):
        if getattr(config, attr) is None and os.environ.get(env_name):
            setattr(config, attr, os.environ[env_name])


def load_config(config_path: Path | None = None) -> NexusConfig:
    """Load configuration from file, auto-creating from env vars if needed."""
    load_env()  # global .env credentials for every entrypoint
    path = config_path or DEFAULT_CONFIG_FILE

    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            data = {}
        config = NexusConfig.from_dict(data)
        _apply_env_defaults(config)
        return config

    config = NexusConfig()

    # Auto-detect Termux
    if os.path.exists("/data/data/com.termux"):
        config.termux_mode = True

    # Auto-detect providers from env vars (global .env included)
    config.providers.update(_detect_env_providers())
    _apply_env_defaults(config)  # search keys and any remaining env defaults

    # Sensible active default: prefer anthropic / opencode-zen (legacy
    # behaviour), else the first detected provider — never a dangling "openai"
    # without a key.
    for preferred in ("anthropic", "opencode-zen"):
        if preferred in config.providers:
            config.active_provider = preferred
            break
    else:
        if config.providers:
            config.active_provider = next(iter(config.providers))

    # Auto-save detected settings. env_sourced API keys serialize as null
    # (ProviderConfig.to_dict), so plaintext keys never land in config.json.
    save_config(config, path)

    return config


def save_config(config: NexusConfig, config_path: Path | None = None) -> None:
    """Save configuration to file (chmod 600 — may contain legacy keys)."""
    path = config_path or DEFAULT_CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(config.to_dict(), f, indent=2)
    _chmod_600(path)


# Load the global .env at import time so every entrypoint (repl/tui/dashboard/
# auth) has credentials before anything constructs a Config.
try:
    load_env()
except Exception:  # pragma: no cover - env loading must never break import
    pass
