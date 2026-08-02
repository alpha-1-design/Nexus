"""Skill Discovery - Nexus explores its own environment and registers capabilities."""

import shutil
import sys
from pathlib import Path


class SkillDiscoverer:
    """Probes the environment to discover and suggest new skills."""

    def __init__(self, nexus_dir: Path):
        self.nexus_dir = nexus_dir
        self.skills_dir = nexus_dir / "skills"
        self.known_tools = {"docker": "docker-manager", "node": "node-dev-tool", "rustc": "rust-expert", "pytest": "test-runner", "sqlite3": "db-admin"}

    def discover(self):
        """Scan system and propose new skills."""
        print("\n[Nexus] Scanning environment for new skills...")
        discovered = []
        for tool, skill in self.known_tools.items():
            if shutil.which(tool):
                if not (self.skills_dir / f"{skill}.py").exists():
                    discovered.append((tool, skill))

        if not discovered:
            print("[\u2713] No new skills discovered. All known tools integrated.")
            return

        print(f"[!] Found new capabilities: {[d[0] for d in discovered]}")

        # Defense in depth: even though callers are expected to gate this
        # behind an interactivity check of their own, never block on a
        # read from a non-TTY stdin here either -- that previously hung
        # any non-interactive invocation (CI, piped output, automation)
        # indefinitely the first time a known tool was detected.
        if not sys.stdin.isatty():
            print("    (non-interactive session -- skipping registration prompts; run 'nexus doctor' interactively to register)")
            return

        for tool, skill in discovered:
            try:
                answer = input(f"Register skill '{skill}' for {tool}? [y/n]: ").lower()
            except EOFError:
                break
            if answer == "y":
                self._register_skill(skill)

    def _register_skill(self, skill_name: str):
        """Simulate registration of a new skill."""
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        (self.skills_dir / f"{skill_name}.py").touch()
        print(f"[\u2713] Skill {skill_name} registered successfully.")
