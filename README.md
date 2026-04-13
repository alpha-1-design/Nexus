# Nexus — AI Coding Agent CLI

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Platform](https://img.shields.io/badge/Platform-Termux%20%7C%20Linux%20%7C%20macOS-orange.svg)

**Nexus** is a powerful, self-hosted AI coding agent CLI that runs anywhere — from a $5 Android phone in Termux to a beefy Linux workstation. It ships with a rich Textual TUI, multi-provider AI support (including completely free models via OpenCode Zen), structured memory, browser automation, voice mode, plugin system, and a built-in Flask dashboard.

## Features

- **Universal AI Providers** — OpenAI, Anthropic, Google Gemini, Groq, DeepSeek, Mistral, Ollama, OpenCode Zen (free models!), OpenCode Go
- **14 Core Tools** — Read, Write, Edit, Glob, Grep, Bash, WebFetch, CodeSearch, TaskRun, DocSearch, BranchCreate, Commit, Review, TodoWrite
- **Browser Automation** — 10 Playwright-based tools: navigate, click, type, screenshot, extract, fill forms, wait, scroll, evaluate JS — with anti-detection stealth patches
- **API Automation** — REST fetch, POST, form extraction, file upload via HTTPX + BeautifulSoup
- **Structured Memory** — Session history, vector embeddings, persistent facts
- **Rich TUI** — 5-panel Textual interface with thinking display, streaming output, and agent status
- **Voice Mode** — TTS + STT with FreeTTS (no API key required) and OpenAI TTS support
- **Dashboard** — Flask REST API + web UI for monitoring and control
- **Plan Mode** — Autonomous multi-step task execution with reflection
- **Plugin System** — Load custom plugins and MCP servers
- **Termux Integration** — Auto-detects Termux, wires up clipboard, notifications, battery, and status bar
- **Safety Engine** — 12 configurable rules to block destructive operations
- **Self-Improvement** — Learns from failures and improves over time
- **Slash Commands** — `/help`, `/voice`, `/sessions`, `/save`, `/plan`, `/think` and more
- **Cross-Device Sync** — Sync sessions and config across devices
- **112 Bundled Skills** — Specialized knowledge for debugging, refactoring, architecture, and more

---

## Installation

### Termux / Android

```bash
# Install dependencies
pkg update && pkg install python git

# Clone the repo
git clone https://github.com/alpha-1-design/rehoboth-genesis.git
cd rehoboth-genesis

# Create a virtual environment
python -m venv venv
source venv/bin/activate

# Install Nexus
pip install -e .

# Install Playwright browser (for browser automation)
nexus automation install-browser
# Or manually: playwright install chromium

# Done!
nexus repl
```

### Ubuntu / Debian / Linux

```bash
# Install system dependencies
sudo apt-get update && sudo apt-get install -y python3 python3-venv git

# Clone the repo
git clone https://github.com/alpha-1-design/rehoboth-genesis.git
cd rehoboth-genesis

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Nexus
pip install -e .

# Install Playwright browser (for browser automation)
nexus automation install-browser
```

### macOS

```bash
# Install system dependencies
brew install python git

# Clone the repo
git clone https://github.com/alpha-1-design/rehoboth-genesis.git
cd rehoboth-genesis

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Nexus
pip install -e .

# Install Playwright browser
nexus automation install-browser
```

### pip (from PyPI — when published)

```bash
pip install nexus-ai
```

---

## Quick Start

### 1. Get Your API Key

**OpenCode Zen (Recommended — FREE models, no key required):**

1. Visit [opencode.ai/zen](https://opencode.ai/zen)
2. Sign up for a free API key (optional but recommended for higher limits)
3. Set the environment variable:
   ```bash
   export OPENCODE_ZEN_API_KEY="your-key-here"
   ```
4. Pick a model. Free models work without any key:
   - `minimax-m2.5-free` — Best free model, great all-around
   - `big-pickle` — OpenCode's own model
   - `qwen3.6-plus-free` — Qwen 3.6B context
   - `nemotron-3-super-free` — NVIDIA's free model

### 2. Run Nexus

```bash
# Interactive REPL
nexus repl

# Run a single task
nexus run "Fix the login bug in auth.py"

# Start the dashboard
nexus dashboard

# Start the TUI
nexus tui

# Check system health
nexus doctor
```

---

## Provider Setup

### OpenCode Zen (Recommended)

The easiest way to get started. Completely free models available.

```bash
export OPENCODE_ZEN_API_KEY="your-key"
export OPENCODE_ZEN_MODEL="minimax-m2.5-free"
```

Or set in config at `~/.nexus/config.json`:

```json
{
  "providers": {
    "opencode-zen": {
      "api_key": "your-key",
      "model": "minimax-m2.5-free",
      "enabled": true
    }
  },
  "active_provider": "opencode-zen"
}
```

### OpenCode Go (Paid subscription)

Premium tier with models like Kimi K2.5, GLM 5, MiniMax M2.7.

```bash
export OPENCODE_GO_API_KEY="your-key"
export OPENCODE_GO_MODEL="kimi-k2.5"
```

### Groq (Free tier available)

```bash
export GROQ_API_KEY="gsk_..."
```

Free models: `llama-3.3-70b-versatile`, `mixtral-8x7b-32768`

### OpenRouter

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

Access to 100+ models including free options.

### Anthropic (Claude)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

Models: `claude-opus-4`, `claude-sonnet-4`, `claude-haiku-3`

### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
```

Models: `gpt-4o`, `gpt-4o-mini`, `gpt-4-turbo`

### Google Gemini

```bash
export GOOGLE_API_KEY="AIza..."
```

### Ollama (Local)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5-coder:7b

export OLLAMA_BASE_URL="http://localhost:11434"
```

---

## Browser Automation

Nexus includes a full Playwright-based browser automation suite with 10 tools and anti-detection stealth measures.

### Setup

```bash
# Install browser (required — Chromium ~180MB)
nexus automation status   # Check status
nexus automation install-browser

# Or manually:
playwright install chromium
```

> **Note:** On Termux/Android, browser automation requires the browser to be installed via `nexus automation install-browser`. The Chromium download may take time on slower connections.

### Available Browser Tools

| Tool | Description |
|------|-------------|
| `browser_navigate` | Navigate to a URL |
| `browser_click` | Click an element by selector |
| `browser_type` | Type text into an element |
| `browser_screenshot` | Capture a screenshot |
| `browser_extract_text` | Extract text matching a selector |
| `browser_fill_form` | Fill out and submit a form |
| `browser_wait` | Wait for an element or timeout |
| `browser_scroll` | Scroll the page |
| `browser_evaluate` | Execute JavaScript |
| `browser_close` | Close the browser |

### Anti-Detection Features

- `navigator.webdriver` property undefined
- Canvas fingerprint noise injection
- Plugin spoofing (Chrome PDF, Word, Excel, etc.)
- User-Agent rotation
- Human-like mouse movement curves
- Keystroke timing simulation
- Scroll pattern mimicking real users
- `Permissions` API override
- Headless mode cloaking
- Custom CSS media feature spoofing

### API Automation

REST API tools for programmatic web interaction:

| Tool | Description |
|------|-------------|
| `api_fetch` | GET request with headers, params |
| `api_post` | POST/PUT/PATCH/DELETE requests |
| `extract_forms` | Parse HTML forms (BeautifulSoup) |
| `api_upload` | Multipart file upload |

Pre-built flows for GitHub, Twitter, Discord, and Slack APIs.

---

## Voice Mode

Enable hands-free voice interaction with TTS (text-to-speech) and STT (speech-to-text).

### FreeTTS (No API key required)

```bash
# FreeTTS uses Microsoft Neural voices — completely free
nexus voice --input "Fix the authentication bug"

# Or in REPL:
/voice Fix the login page
```

### OpenAI TTS (Requires API key)

```bash
export OPENAI_API_KEY="sk-..."
nexus voice --input "Run tests" --tts-provider openai
```

---

## CLI Reference

```bash
nexus repl                 # Interactive REPL (default)
nexus run <task>          # Run a single task and exit
nexus tui                 # Start the Textual TUI
nexus dashboard           # Start the Flask dashboard
nexus doctor              # System health check
nexus automation status   # Check automation status
nexus automation install-browser  # Install Playwright Chromium
nexus voice <input>       # Voice mode
nexus skills list         # List available skills
nexus skills search <query>  # Search skills
nexus sessions list       # List saved sessions
nexus sessions load <id>  # Load a session
nexus config show         # Show current config
nexus config set <key> <value>  # Set a config value
```

### REPL Slash Commands

```
/help              Show all slash commands
/voice             Toggle voice mode
/sessions          List and manage sessions
/save              Save current session
/plan              Enter plan mode
/think             Show thinking engine output
/context           Show current context
/tools             List available tools
/clear             Clear the screen
/exit              Exit the REPL
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Nexus CLI                            │
│    repl │ run │ tui │ dashboard │ voice │ doctor        │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│                  Agent Orchestrator                      │
│    Thinking Engine  │  Reflection  │  Safety Engine     │
└───────────────┬─────────────────────────────────────────┘
                │
    ┌───────────┼────────────┐
    │           │            │
┌───▼───┐  ┌───▼────┐  ┌───▼──────┐
│Memory │  │Tools   │  │Providers │
│Session│  │Core 14 │  │OpenAI    │
│Facts  │  │Browser10│  │Anthropic │
│Vector │  │API    4 │  │OpenCode  │
└───────┘  │Termux  │  │Gemini    │
           │MCP     │  │Groq      │
           └────────┘  └──────────┘
```

### Provider Layer

Nexus uses a unified `BaseProvider` interface. Adding a new provider requires:

1. Add entry to `PROVIDER_REGISTRY` in `nexus/providers/base.py`
2. The provider is accessed through `ProviderManager` and returns `Message`, `ToolCall`, `Response` objects

### Tool System

All tools inherit from `BaseTool` (ABC) and must implement `@property def definition(self) -> ToolDefinition`. Tools are registered in `nexus/tools/core.py` via `register_all(registry)`.

### Memory System

- `Session` — Chat history with timestamps and metadata
- `Fact` — Structured knowledge entries with tags
- `VectorMemory` — Embedding-based semantic search

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENCODE_ZEN_API_KEY` | OpenCode Zen API key (get at opencode.ai/zen) |
| `OPENCODE_ZEN_MODEL` | Model name for OpenCode Zen |
| `OPENCODE_GO_API_KEY` | OpenCode Go subscription key |
| `OPENCODE_GO_MODEL` | Model name for OpenCode Go |
| `OPENAI_API_KEY` | OpenAI API key |
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `GOOGLE_API_KEY` | Google API key |
| `GROQ_API_KEY` | Groq API key |
| `OPENROUTER_API_KEY` | OpenRouter API key |
| `OLLAMA_BASE_URL` | Ollama base URL (default: http://localhost:11434) |
| `NEXUS_CONFIG_DIR` | Custom config directory |
| `NEXUS_TERMUX_MODE` | Force Termux mode (0 or 1) |

---

## Termux-Specific Setup

When Nexus detects Termux, it automatically enables:

- **Clipboard** — `termux-clipboard-set`, `termux-clipboard-get`
- **Notifications** — `termux-notification`
- **Battery** — `termux-battery-status`
- **Status Bar** — `termux-wallpaper`, `termux-volume`
- **Microphone** — For voice input

Install Termux:API for full integration:

```bash
pkg install termux-api
```

Auto-detection happens in `nexus/config.py` by checking for `/data/data/com.termux/files/usr/bin/termux-audio`.

---

## Configuration

Config is stored at `~/.nexus/config.json` and can also be set via environment variables:

```bash
# Via environment (takes precedence over config file)
export OPENCODE_ZEN_MODEL="big-pickle"
export NEXUS_LOG_LEVEL="DEBUG"
```

Or interactively in the REPL:

```
/config set active_provider opencode-zen
/config set tool_profile coding
/config show
```

---

## Contributing

Contributions are welcome! Please follow these steps:

1. **Fork** the repository
2. **Create a feature branch**: `git checkout -b feature/amazing-feature`
3. **Make your changes** — follow existing code style
4. **Run tests**: `pytest tests/`
5. **Lint**: `ruff check nexus/`
6. **Commit**: `git commit -m 'Add amazing feature'`
7. **Push**: `git push origin feature/amazing-feature`
8. **Open a Pull Request**

### Development Setup

```bash
git clone https://github.com/alpha-1-design/rehoboth-genesis.git
cd rehoboth-genesis
python -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
ruff check nexus/
pytest tests/
```

---

## Partnership Program

We're building a thriving ecosystem around Nexus. Partners include:

- **OpenCode AI** — Free and premium model access
- **Model providers** — Groq, OpenRouter, Anthropic, Google, OpenAI
- **Community contributors** — Skills, plugins, integrations

Interested in partnering? Open an issue or reach out via GitHub Discussions.

---

## License

**MIT License** — see [LICENSE](LICENSE) file for details.

Copyright (c) 2025-2026 Rehoboth Genesis Team

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.

---

## Commit History

```bash
git log --oneline -10
```

### Latest Release: Nexus Genesis v0.1.0

- Universal AI provider support including OpenCode Zen (free models)
- 14 core tools + 14 browser/API automation tools
- Rich Textual TUI with 5-panel layout
- Structured memory with vector search
- Voice mode with FreeTTS (no API key required)
- Flask dashboard with REST API
- Plugin system and MCP server support
- Termux auto-detection and integration
- 12-rule safety engine
- 112 bundled skills
- Cross-platform: Android/Termux, Linux, macOS
