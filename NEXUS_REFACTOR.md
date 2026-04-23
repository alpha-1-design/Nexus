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
