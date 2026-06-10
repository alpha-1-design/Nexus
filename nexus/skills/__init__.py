"""Skills system - load, apply, and install domain-specific skills.

Skills are discovered from a directory of Markdown files (`.md`) with YAML frontmatter.
Community skills can be installed from a remote registry (GitHub).
"""

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Skill:
    """A domain-specific skill loaded from a .md file."""

    name: str
    description: str
    content: str
    category: str = "general"
    tools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    version: str = "1.0"
    priority: int = 0
    source_file: str = ""


@dataclass
class SkillsConfig:
    """Configuration for the skills system."""

    skills_dir: Path | None = None
    auto_load: bool = True
    max_skills: int = 50
    tag_filters: list[str] = field(default_factory=list)


class SkillLoader:
    """
    Discovers and parses skill files from a directory.
    Each skill is a .md file with YAML frontmatter.
    """

    FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)", re.DOTALL)
    SKILL_DIRS = [
        Path.home() / ".config" / "opencode" / "skills",
        Path.home() / ".nexus" / "skills",
        Path(__file__).parent.parent.parent / "skills",
    ]

    def __init__(self, skills_dir: Path | None = None):
        self.skills_dir = skills_dir or self._find_default_dir()
        self._cache: dict[str, Skill] = {}
        self._loaded = False

    def _find_default_dir(self) -> Path | None:
        """Find the first existing skills directory."""
        for d in self.SKILL_DIRS:
            if d.exists() and d.is_dir():
                return d
        return None

    def discover(self) -> list[Skill]:
        """Discover all skills in the skills directory."""
        skills = []
        if not self.skills_dir or not self.skills_dir.exists():
            return skills

        for path in self.skills_dir.rglob("*.md"):
            try:
                skill = self._parse_skill_file(path)
                if skill:
                    skills.append(skill)
            except Exception:
                continue

        skills.sort(key=lambda s: (-s.priority, s.name))
        self._cache = {s.name: s for s in skills}
        self._loaded = True
        return skills

    def _parse_skill_file(self, path: Path) -> Skill | None:
        """Parse a single skill file with YAML frontmatter."""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None

        match = self.FRONTMATTER_RE.match(content)
        if match:
            frontmatter_raw, body = match.groups()
            try:
                fm = yaml.safe_load(frontmatter_raw) or {}
            except yaml.YAMLError:
                fm = {}
        else:
            fm = {}
            body = content

        return Skill(
            name=str(fm.get("name", path.stem)),
            description=str(fm.get("description", "")),
            content=body.strip(),
            category=str(fm.get("category", "general")),
            tools=list(fm.get("tools", [])),
            tags=list(fm.get("tags", [])),
            version=str(fm.get("version", "1.0")),
            priority=int(fm.get("priority", 0)),
            source_file=str(path),
        )

    def get(self, name: str) -> Skill | None:
        """Get a skill by name."""
        if not self._loaded:
            self.discover()
        return self._cache.get(name)

    def get_by_tag(self, tag: str) -> list[Skill]:
        """Get all skills with a specific tag."""
        if not self._loaded:
            self.discover()
        return [s for s in self._cache.values() if tag in s.tags]

    def get_by_category(self, category: str) -> list[Skill]:
        """Get all skills in a category."""
        if not self._loaded:
            self.discover()
        return [s for s in self._cache.values() if s.category == category]

    def format_for_prompt(self, skills: list[Skill], max_chars: int = 8000) -> str:
        """
        Format skills as a prompt section.
        Truncates to max_chars to avoid context overflow.
        """
        if not skills:
            return ""

        sections = ["## Activated Skills\n"]
        for skill in skills:
            sections.append(f"### {skill.name}\n{skill.content}\n")

        combined = "\n".join(sections)
        if len(combined) > max_chars:
            combined = combined[:max_chars] + f"\n\n[... {len(skills)} skills loaded, truncated ...]"
        return combined


class SkillsManager:
    """
    Manages loaded skills and provides skill context to the agent.
    Skills can be activated for specific tasks or always available.
    """

    def __init__(self, config: SkillsConfig | None = None):
        self.config = config or SkillsConfig()
        self.loader = SkillLoader(self.config.skills_dir)
        self._active: list[Skill] = []
        self._always_on: list[str] = []
        self._loaded: list[Skill] = []

    def load_all(self) -> list[Skill]:
        """Load all discoverable skills."""
        self._loaded = self.loader.discover()
        return self._loaded

    def activate(self, skill_name: str) -> bool:
        """Activate a skill by name."""
        skill = self.loader.get(skill_name)
        if not skill:
            return False
        if skill not in self._active:
            self._active.append(skill)
        return True

    def deactivate(self, skill_name: str) -> bool:
        """Deactivate a skill by name."""
        for i, s in enumerate(self._active):
            if s.name == skill_name:
                self._active.pop(i)
                return True
        return False

    def activate_by_tags(self, tags: list[str]) -> None:
        """Activate all skills matching any of the given tags."""
        for skill in self._loaded:
            if any(t in skill.tags for t in tags) and skill not in self._active:
                self._active.append(skill)

    def activate_by_category(self, category: str) -> None:
        """Activate all skills in a category."""
        for skill in self._loaded:
            if skill.category == category and skill not in self._active:
                self._active.append(skill)

    def auto_activate(self, task: str) -> None:
        """
        Auto-activate skills based on task keywords.
        Simple heuristic matching.
        """
        task_lower = task.lower()
        keyword_map = {
            r"\bapi\b|\brest\b|\bendpoint\b": ["api-endpoint-builder"],
            r"\bbug\b|\bfix\b|\berror\b|\bcrash\b": ["bug-hunter"],
            r"\bsecurity\b|\baudit\b|\bcve\b": ["audit-skills", "aws-security-audit"],
            r"\bastro\b": ["astro"],
            r"\bsvelte\b": ["sveltekit"],
            r"\bhono\b": ["hono"],
            r"\btest\b|\bload\b|\bperformance\b": ["k6-load-testing", "performance-optimizer"],
            r"\brelease\b|\bchangelog\b|\bgit\b": ["git-release"],
            r"\b(prompt|skill)\b": ["skill-check"],
            r"\baws\b|\biam\b": ["aws-iam-best-practices"],
        }
        for pattern, skill_names in keyword_map.items():
            if re.search(pattern, task_lower):
                for name in skill_names:
                    self.activate(name)

    def get_context(self, max_chars: int = 8000) -> str:
        """Get formatted skill context for the current session."""
        skills = self._active + [s for s in self._active if s.name in self._always_on]
        return self.loader.format_for_prompt(skills, max_chars)

    def list_active(self) -> list[str]:
        """List names of active skills."""
        return [s.name for s in self._active]

    def list_all(self) -> list[Skill]:
        """List all loaded skills."""
        return self._loaded.copy()

    def list_categories(self) -> list[str]:
        """List all available skill categories."""
        return list({s.category for s in self._loaded})

    def search(self, query: str) -> list[Skill]:
        """Search skills by name, description, or tags."""
        q = query.lower()
        return [s for s in self._loaded if q in s.name.lower() or q in s.description.lower() or q in " ".join(s.tags).lower()]

    # --- Community skill registry ---

    COMMUNITY_REGISTRY_BASE = "https://raw.githubusercontent.com/alpha-1-design/nexus-skills/main"

    def list_community(self) -> list[dict[str, str]]:
        """Fetch the community skill registry index.

        Returns a list of dicts with keys: name, description, category, tags, version.
        """
        import json
        import urllib.request
        url = f"{self.COMMUNITY_REGISTRY_BASE}/index.json"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return json.loads(resp.read().decode())
        except Exception:
            return []

    def search_community(self, query: str) -> list[dict[str, str]]:
        """Search community skills by name/description/tags."""
        q = query.lower()
        all_skills = self.list_community()
        return [
            s for s in all_skills
            if q in s.get("name", "").lower()
            or q in s.get("description", "").lower()
            or q in " ".join(s.get("tags", [])).lower()
        ]

    def install_community(self, skill_name: str) -> dict[str, str]:
        """Install a community skill by name.

        Downloads the .md file from the community registry and places it
        in the user's skills directory so it's auto-discovered.
        """
        import urllib.request

        all_skills = self.list_community()
        match = next((s for s in all_skills if s.get("name") == skill_name), None)
        if not match:
            return {"status": "error", "message": f"Skill '{skill_name}' not found in community registry"}

        # Determine install dir
        install_dir = Path.home() / ".nexus" / "skills"
        install_dir.mkdir(parents=True, exist_ok=True)

        # Download the skill file
        filename = match.get("file", f"{skill_name}.md")
        url = f"{self.COMMUNITY_REGISTRY_BASE}/skills/{filename}"
        dest = install_dir / filename

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                content = resp.read().decode("utf-8")
            dest.write_text(content, encoding="utf-8")
            # Re-discover to register
            self.load_all()
            self.activate(skill_name)
            return {
                "status": "success",
                "message": f"Installed '{skill_name}' ({match.get('version', '1.0')})",
                "path": str(dest),
            }
        except Exception as e:
            status = "error"
            msg = str(e)
            if hasattr(e, "code"):
                msg = f"Download failed: HTTP {e.code}"
            return {"status": status, "message": msg}

    def uninstall(self, skill_name: str) -> dict[str, str]:
        """Remove an installed skill file."""
        for skill in self._loaded:
            if skill.name == skill_name:
                path = Path(skill.source_file)
                try:
                    path.unlink()
                    self.deactivate(skill_name)
                    self.load_all()
                    return {"status": "success", "message": f"Uninstalled '{skill_name}'"}
                except Exception as e:
                    return {"status": "error", "message": str(e)}
        return {"status": "error", "message": f"Skill '{skill_name}' not found"}
