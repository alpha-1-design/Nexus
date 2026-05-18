# Contributing to Nexus

Thank you for your interest in contributing to **Nexus**! We are building a decentralized, agentic OS, and your contributions help make it more resilient, intelligent, and secure.

## How Can I Contribute?

### 1. Developing Agents & Skills
*   Create new agents by inheriting from `NexusAgent`.
*   Register skills in `nexus/skills/` following the existing interface patterns.
*   Ensure all new agents are tested within the `tests/` directory.

### 2. Improving Resilience
*   Help us harden the `ResilienceEngine` by submitting logs of unexpected failures or edge cases.
*   Improve the self-improvement loop in `self_improve.py`.

### 3. Pull Requests
1.  Fork the repo and create your feature branch.
2.  Follow the existing Python (PEP 8) code style.
3.  Ensure your changes maintain the performance of the TUI and the neural orchestration.
4.  Submit your PR with a clear description and testing artifacts.

## Engineering Standards

*   **Asynchronous Excellence:** Nexus is an async-first OS. Keep all blocking operations off the main loop.
*   **Neural Governance:** Changes to core governance (`core.py`) require thorough review to ensure agent stability.
*   **Performance:** The CLI TUI must remain responsive under heavy LLM workloads.

Happy coding!
