# Nexus

Your AI coding partner. Runs on your phone, laptop, or server.

```bash
git clone https://github.com/alpha-1-design/rehoboth-genesis.git && cd rehoboth-genesis
python -m venv venv && source venv/bin/activate && pip install -e .
nexus repl
```

That's it. OpenCode Zen free models work without an API key.

---

## Commands

```
nexus repl                  # Chat with AI
nexus run "fix the login"  # One-shot task
nexus dashboard             # Web UI (optional)
nexus setup                # Configure provider
nexus doctor               # Check setup
```

## Providers

- **OpenCode Zen** — free models, works immediately (default)
- **Groq** — free tier, set `GROQ_API_KEY`
- **OpenAI** — set `OPENAI_API_KEY`
- **Anthropic** — set `ANTHROPIC_API_KEY`
- **Gemini** — set `GOOGLE_API_KEY`
- **Ollama** — local models, no API key

## Browser Automation

```bash
nexus automation install-browser
nexus repl
# Agent can browse, click, type, screenshot
```

## Termux / Android

Same commands work. Auto-detects Termux, enables clipboard and notifications.

---

Questions? Open an issue at https://github.com/alpha-1-design/rehoboth-genesis
