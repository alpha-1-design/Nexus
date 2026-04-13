# Nexus

**A self-hosted AI coding agent CLI that actually works out of the box.**

No boilerplate. No cloud lock-in. No "works on my machine" surprises. Nexus runs on anything with Python 3.10+ — including that old laptop gathering dust, a cheap Android phone via Termux, or a $5/month VPS.

```bash
git clone https://github.com/alpha-1-design/rehoboth-genesis.git && cd rehoboth-genesis
pip install -e .
nexus repl
```

That's the entire setup. OpenCode Zen free models work immediately with zero API keys.

---

## Why Nexus?

Most AI coding tools assume you have a beefy development machine, a paid API subscription, and hours to debug environment issues. Nexus flips that.

- **Works where you are.** Chromebook? Old MacBook? Android phone running Termux? A Raspberry Pi? Nexus doesn't care.
- **Zero-config first run.** No API keys required — free OpenCode Zen models work on launch. Bring your own keys if you want, but you don't have to.
- **Self-hosted dashboard.** Optional web UI for visualization, session history, and browser automation. Your data stays on your machine.
- **Real browser automation.** Playwright-powered agent can browse, click, type, and screenshot. Useful for automating web tasks or scraping without fighting anti-bots.
- **Termux-native.** Clipboard integration, notification hooks, and a compact TUI mode designed for small phone screens.
- **Memory that persists.** Sessions, learnings, and facts survive restarts. The agent gets better over time.
- **Tool-augmented.** File editing, bash execution, git operations, web search, and browser control built-in.

---

## Commands

| Command | Description |
|---|---|
| `nexus repl` | Interactive chat session with the AI |
| `nexus run "fix the login bug"` | Run a single task and exit |
| `nexus dashboard` | Start the web UI (optional) |
| `nexus setup` | Interactive provider configuration |
| `nexus doctor` | Diagnose setup and connectivity |
| `nexus tui` | Compact terminal UI for small screens |
| `nexus sync` | Sync sessions across machines |
| `nexus learn` | Show learnings and improvement queue |

**Slash commands (inside REPL):**

```
/help           Show all commands
/exit           Leave the session
/clear          Wipe conversation
/tools          List available tools
/model <name>   Switch model mid-session
/fact <k> <v>   Store a persistent fact
/save           Persist current session
```

---

## Providers

Nexus uses OpenAI-compatible provider abstraction. Configure one or more:

| Provider | API Key | Free Tier | Notes |
|---|---|---|---|
| **OpenCode Zen** | None | Yes (default) | Works immediately |
| **Groq** | `GROQ_API_KEY` | Yes | Fast inference |
| **OpenAI** | `OPENAI_API_KEY` | No | Full model range |
| **Anthropic** | `ANTHROPIC_API_KEY` | No | Claude models |
| **Gemini** | `GOOGLE_API_KEY` | Limited | Google's models |
| **Ollama** | None | Yes | Local models |
| **Deepseek** | `DEEPSEEK_API_KEY` | Limited | Cost-effective |
| **Custom** | Varies | Varies | Any OpenAI-compatible API |

```bash
# Quick setup with OpenCode Zen (default)
nexus setup

# Or configure manually
nexus provider add groq --type groq --api-key $GROQ_API_KEY --model llama-3.3-70b

# Switch active provider
nexus provider set-active groq
```

---

## Termux / Android

Nexus is designed to work well on Android via Termux.

```bash
pkg install python git
git clone https://github.com/alpha-1-design/rehoboth-genesis.git
cd rehoboth-genesis
pip install -e .
nexus repl
```

**Termux-specific features:**
- Clipboard read/write integration
- Push notification hooks (via Termux:API)
- Compact TUI mode (`nexus tui`) for small screens
- Auto-detects Termux environment and adjusts output

No need to SSH into a remote machine just to use an AI coding agent. Run it locally on your phone.

---

## Browser Automation

```bash
# One-time browser install
nexus automation install-browser

# Then use it in a session
nexus repl
# Agent can now browse URLs, click elements, fill forms, take screenshots
```

Powered by Playwright with stealth mode: randomized user-agents, human-like mouse curves, keystroke timing, and built-in CAPTCHA detection.

---

## Architecture

```
nexus/
├── cli/          REPL, TUI, and command interface
├── providers/    AI provider abstraction (OpenAI-compatible)
├── tools/       File editing, bash, git, search, browser
├── memory/      SQLite-backed session and fact storage
├── dashboard/   Optional Flask web UI
├── automation/  Browser and API automation
└── agents/      Multi-agent orchestration
```

---

## Configuration

Config lives at `~/.nexus/config.json`. You can also pass `--config /path/to/config` to any command.

```bash
nexus config --show    # Print current config
nexus config --edit    # Open in editor
```

---

## License

MIT
