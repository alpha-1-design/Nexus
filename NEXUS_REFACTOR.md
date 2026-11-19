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


## 2026-11-19 Session 3 — Settings + Themes + Agentic Order of Service

**592 tests passing** (up from 365).

1. **Settings + preferences (A):** new `nexus/tui/settings.py` — `TUISettings` dataclass (theme, timestamps 24h/12h/off, show_diffs, streaming_cursor, compact_mode, spinner_style, pinned_panels, sidebar_animations) persisted at `~/.nexus/settings.json` (atomic tmp+replace, corrupt/missing → defaults, unknown keys ignored with a note surfaced as a system message at boot). Singleton `get_settings()`. `/settings` + ctrl+s push `SettingsModal` (`nexus/tui/settings_screen.py`): keyboard-first (↑↓/tab move, enter/←→ cycle, esc close), grouped Appearance/Behavior/Panels, every change applies LIVE (`NexusTUI._apply_live_settings` → theme apply, transcript rerender, panel pin restore) and persists immediately. `/theme [name]` sets/cycles themes; both commands in `/help`, the slash dropdown and the palette.
2. **Themes (B):** 5 built-ins in `nexus/tui/colors.py` — neural-cyan (default), synthwave, matrix, amber-crt, mono — each defining the FULL token set (`REQUIRED_TOKENS` enforced; partial maps rejected). `apply_theme()` mutates `CSS_COLORS` **in place** so every `from .colors import CSS_COLORS` reader swaps live; widgets re-resolve frozen dicts (`_refresh_theme_colors`) via the `on_theme_change` listener seam; `WelcomeCard.refresh_theme()` retints the logo. Diff +green/-red stay semantic in all themes (tinted; mono uses desaturated tints). `styles.css` untouched — dynamic colors come only from the token layer. Provider brand colors override `logo_tint` in every theme; `welcome.py get_logo()` follows provider → theme token (loaded dependency-free via importlib so the CLI never imports Textual).
3. **Agentic order of service (C):** `BootChecklist` widget prints the subsystem checklist one line per ~150ms above the welcome card — `◆ memory … ✓ 3 facts`, `◆ skills … ✓ 382 loaded`, `◆ tools … ✓ 24 registered`, `◆ mcp … ✓ N servers` (from `~/.nexus/mcp-servers.json`), `◆ provider … ✓ groq` or `— none configured (/auth)`; failures render `✗ <reason>` in amber and never crash boot; `nexus.discovery.boot_summary()` overrides counts when the parallel module ships (ImportError-safe seam). Assistant messages carry the provenance row `✦ Nexus · groq · llama-3.3-70b · 3.2s` (users/system stay compact). Running tool cards animate spinner glyphs (braille/dots/pulse per settings) off a shared ChatPanel timer; streaming cursor is settings-gated; ThinkingPanel header shows `◆ working…` while steps run. StatusBar adds cached `⎇ <branch>` (`git rev-parse`, 2s timeout, `—` outside repo) + `#<short session id>` and fits one line at 90 cols (flexible tail parts truncate: message → cwd → session → branch → model last).

Capture: `scripts/capture_tui.py --theme NAME [--provider NAME MODEL]` (old positional flags kept) → `download/tui_default.svg`, `tui_synthwave.svg` (90x30), `tui_matrix.svg` (groq brand override) — all >40k bytes with theme accent verified in the SVG. Follow-ups: styles.css chrome background stays neural-dark (token layer only, per design); model-name truncation in the status bar is the last-resort fit; welcome-card expanded tool-card state resets on theme rerender.

## 2026-11-19 Session 4 — Feature Completion Wave

**627 tests passing** (up from 592). Net −382 lines while adding features.

1. **Compaction:** orchestrator helpers (`estimate_context_tokens`, `kept_suffix`, `summarize_messages_deterministic`, `COMPACT_*` constants) + `context_usage/should_compact/compact_context/_summarize_messages`; REPL `/compact [focus]` + `/context` (mini bar), auto-trigger at turn start. Integration fixes: `kept_suffix` rewritten to keep last 8 with dangling-tool trim (the anchored-at-user-turn version kept only 1 message); compaction test aligned to the keep-8 contract.
2. **Strong memory (M5):** `SimpleKeywordBackend` now real per-field BM25 (content 1.0 + tags 1.5, k1=1.5, b=0.75, +1.5 phrase bonus, recency boost `1+0.25·exp(-age/14d)`), same public interface; FTS5 external-content sync fixed (invalid `'reindex'` SQL → documented rowid-mirror insert; FTS delete added to `_delete_sync`); preference auto-capture (always/never/prefer → deduped Fact, source "auto", confidence 0.8).
3. **M6 purge:** `nexus/orchestrator/` (−1,357) and `nexus/resilience/` (−317) deleted after zero-importer greps; dead `decomposer`/`executor` imports, init lines, `_execute_tool_callback`/`_execute_llm_callback` and `run_complex_task` removed from `agent/orchestrator.py` (wave-2 agent's grep missed the import — fixed in integration); voice kept (REPL imports it).
4. **Circuit breaker:** per-provider in `providers/manager.py` (3 consecutive failures → 120s open → half-open single probe; composes with 429 rotation via `_rotation_candidates`); `breaker_state(name)` public.
5. **SSE MCP transport:** stdlib http.client SSE client (endpoint discovery via urljoin, id-matched JSON-RPC, `NEXUS_MCP_TIMEOUT`, one stale-endpoint retry, graceful ToolResults); shares tools/list + tools/call mapping with stdio; hermetic threading http.server tests (fixture no-endpoint case fixed in integration).
6. **Docs/screenshots:** README features + architecture rewritten to match shipped reality (honesty sweep: circuit-breaker claim now describes the real per-provider breaker); new `tui-boot.png` (agentic boot checklist) + `tui-synthwave.png` screenshots; handoff in download/NEXUS_SESSION3_HANDOFF.md.
