# Nexus Refactor Manifest: "The Lean Nexus"

**Objective:** Transform Nexus from a complex, brittle multi-agent system into a reliable, fast, and mobile-optimized "ReAct" style coding agent.

## Core Architectural Shift
- **From:** Pre-planned "Waterfall" Task Plans (Decomposer -> Executor).
- **To:** Reactive Loop (Observe -> Think -> Act). The agent should decide its next move based on the *result* of the previous tool call, not a guess made 5 steps ago.

## Current Technical Debt (High Priority)
1.  **Brittle Editing:** `EditTool` requires 100% exact string matching. **Fix:** Move to a line-based or block-based edit tool with context fuzzy-matching. (Partially addressed, now context-aware).
2.  **Orchestration Overhead:** Too many layers (Thinking, Safety, Resilience). **Fix:** Flatten the core loop. Move logic into the System Prompt where possible. (Core loop refactored to ReAct).
3.  **Directory Confusion:** `src/nexus/` vs `nexus/` at root. **Fix:** Consolidate to a standard `src/` layout and fix `pyproject.toml`. (Completed, code moved to root `nexus/`).
4.  **Mobile Bottlenecks:** Heavy dependencies (Playwright, Faster-Whisper) should be lazy-loaded plugins, not core requirements. (Dependency Guard utility created, needs feature integration).

## Completed Integrations & Refinements

### Dynamic Features & UX
*   **ReAct Loop:** Replaced the Decomposer/Executor with a fluid, real-time Reason->Act->Observe loop in `nexus/cli/repl.py`.
*   **Atmospheric UI:**
    *   **REPL:** Branded prompts, atmospheric loading/tool indicators, sci-fi styling for messages and permissions.
    *   **TUI:** "Neural Activity" pulse in StatusBar, pulsing "ThinkingPanel" animation.
    *   **Startup:** Branded ASCII logo and cinematic "boot sequence" in `welcome.py`.
*   **Proactive Dependency Management:** `ensure_dependency` utility created and integrated into `/voice` command and automation tools. Nexus now prompts for installation of missing optional libraries.
*   **Automatic Update Notifications:** REPL startup now checks for updates and notifies the user.
*   **Voice Command Integration:** `/voice` command is functional and handles dependency checks.
*   **Provider Fallbacks:** `ProviderManager` supports automatic switching between LLM providers.
*   **Bug Fixes:** Resolved `ImportError`s, `AttributeError`, and improved tool reliability.

## Instructions for Future Agents

1.  **Always read this file first.**
2.  **Prioritize Reliability & Dynamism:** Continue refining the ReAct loop. Ensure tools are robust and responsive.
3.  **Seamless Dependency Handling:** Hook `ensure_dependency` into *all* features that rely on optional external libraries (e.g., advanced automation, specific model backends).
4.  **TUI Enhancements:** Continue polishing TUI animations and layout for maximum impact on mobile.
5.  **Modularize Features:** Ensure heavy extensions (Voice, Automation, Dashboard) are truly optional and load lazily.
6.  **Testing:** Implement comprehensive unit and integration tests for the ReAct loop and tool interactions.

The core engine is now dynamic and the UI is immersive. The project is in a solid state for further feature development and refinement.

## 2026-11-19 Strike-Team Session — "Billion-Dollar Polish" Round

Five subsystems rebuilt/fixed in parallel; **288 tests passing** (baseline 175).

1. **Agent loop (M1 fixed):** real ReAct loop (max 8 tool rounds), streamed tool-call arguments accumulated correctly (`providers/base.py` `_StreamToolCallBuffer`), manager.stream supports tools with pre-emission fallback only, valid transcripts (assistant+tool_calls before role=tool), every turn persisted to history+session, `/abort` cooperative, `/model` awaits switch. Recon: download/NEXUS_RECON_FINDINGS.md has the full file:line receipts.
2. **TUI v2:** Claude-Code-class — timestamps, collapsible tool cards, unified diff rendering (edit/write results emit diffs via tools/core.py), singleton orchestrator wiring fixed (panels go live), panels hide when empty, chat 1fr with min-height, status bar (model/provider/ctx/tokens/cwd), welcome card, streaming cursor. Known limitation: orchestrator only streams no-tool turns; approval flow still display-only.
3. **Free providers + rotation + .env:** 9 free-tier providers registered with verified endpoints (Groq, OpenRouter, Cerebras, Gemini, Mistral, GitHub Models, HuggingFace, Cloudflare, Zhipu), 429/quota rotation with 120s cooldowns + `last_rotation_note`, `~/.nexus/.env` loader (stdlib parser, chmod 600, env-sourced keys never rewritten into config.json), auth wizard recommends Auto free rotation. FLAGGED: verify GitHub Models / Cloudflare / Zhipu endpoints + live 429 rotation on real devices.
4. **/init:** deterministic project scanner → `NEXUS.md` (git, languages, manifests, build commands, repo map, conventions), backup-rotate on regen, injected into REPL + orchestrator prompts (4000-char cap), `nexus init` CLI subcommand, TUI autocomplete. Old `nexus init` scaffolding is shadowed — consider renaming to `nexus scaffold`.
5. **Installers:** `install.sh` dispatcher → `install-termux.sh` / `install-laptop.sh` (flags: --with-browser, --dev, --uninstall). FLAGGED: venv + psutil source-build edge cases on real Termux.
6. **Infra fixes:** memory facts.json/sessions now corrupt-tolerant + atomic writes (boot never blocks on truncated files).

Remaining known debt (see NEXUS_RECON_FINDINGS.md M3–M6): MCP handshake swapped-args bug + adapter never registered, safety rules never match commands, GitTool shell=True injection, bash sandbox no-op, "semantic recall" still keyword LIKE, ~1,500 lines dead code (orchestrator/, circuit_breaker, voice stub), dashboard unauthenticated write route.

## 2026-11-19 Session 2 — M3 Strike + Identity/Skills Round

**365 tests passing** (up from 288).

1. **M3 — MCP now actually works:** the 3 swapped `run_in_executor` args fixed (proper id-matched responses, JSON-RPC errors surfaced, `isError` mapped to failed ToolResult, request timeouts via `NEXUS_MCP_TIMEOUT`, stderr drained, dead servers reaped). `MCPAdapterTool` registration wired: `tools/base.py` `_register_core_tools` lazily calls `register_mcp_tools(registry)` per `~/.nexus/mcp-servers.json` — one adapter per server, lazy connect on first call, graceful per-server failure. 26 hermetic tests with REAL stdio JSON-RPC subprocesses (good/garbage/hung/dying servers).
2. **Provider identity:** `PROVIDER_BRAND_COLORS` map (14 providers + aliases); TUI welcome logo + `⬢ provider · model` badge, status bar `● provider` and REPL boot badge tint with the connected provider's brand color; neutral cyan when none.
3. **Skill pre-loading:** new `nexus/skills/matcher.py` — token-overlap scorer over the 382-skill catalog (~24ms cold, <0.1ms warm, threshold-gated top-3). Orchestrator + REPL inject a capped `### RELEVANT SKILLS` section before the first LLM call of a turn; TUI shows `⚡ N skills loaded: …`, REPL prints a dim skills line, `/skills <task>` command + autocomplete added, usage recorded (`Turn.skills_loaded`, `get_skill_usage()`).
4. Pending follow-ups: `commands.py` should use `collect_server_status` for `nexus mcp list`; `welcome.py get_logo()` still hardcoded cyan; `nexus skills match` CLI subcommand; SSE transport only does initialize; dashboard provider tint skipped.


