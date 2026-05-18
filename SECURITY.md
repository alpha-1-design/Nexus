# Security Policy

## Reporting Vulnerabilities

As an autonomous AI agent, Nexus has privileged access to your filesystem and environment. Security is our top priority.

If you discover a vulnerability or a potential exploit in Nexus, please report it privately:

1.  **Email:** [support@alpha1studio.com]
2.  **Privacy:** We will acknowledge your report within 48 hours and work with you to patch the issue before making it public.

## Security Architecture

*   **Sandboxing:** Nexus runs tools in a controlled environment. However, use caution when running agents on sensitive data.
*   **Credential Handling:** API keys are managed by the user; they are never sent to external servers except to the LLM provider you explicitly configure.
*   **Governance:** The `NexusDoctor` module regularly audits system health to detect anomalous tool behavior.
