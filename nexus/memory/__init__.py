"""Structured memory system for Nexus.

Provides session management, facts storage, and project context.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
import uuid


@dataclass
class Session:
    """A conversation session."""
    id: str
    created_at: datetime
    updated_at: datetime
    messages: list[dict[str, Any]] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    outcome: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "messages": self.messages,
            "tools_used": self.tools_used,
            "outcome": self.outcome,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=data["id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            messages=data.get("messages", []),
            tools_used=data.get("tools_used", []),
            outcome=data.get("outcome"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class Fact:
    """A stored fact about the user or project."""
    key: str
    value: Any
    category: str = "general"
    confidence: float = 1.0
    source: str = "session"
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "category": self.category,
            "confidence": self.confidence,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }


class Memory:
    """Structured memory manager."""

    def __init__(self, memory_dir: Path | None = None):
        from ..config import DEFAULT_MEMORY_DIR
        self.memory_dir = memory_dir or DEFAULT_MEMORY_DIR
        self.sessions_dir = self.memory_dir / "sessions"
        self.projects_dir = self.memory_dir / "projects"
        self.facts_file = self.memory_dir / "facts.json"
        self.todos_file = self.memory_dir / "todos.json"

        self._ensure_dirs()
        self._facts: dict[str, Fact] = {}
        self._load_facts()

    def _ensure_dirs(self) -> None:
        """Ensure memory directories exist."""
        for d in [self.sessions_dir, self.projects_dir, self.memory_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def _load_facts(self) -> None:
        """Load facts from disk."""
        if self.facts_file.exists():
            with open(self.facts_file) as f:
                data = json.load(f)
                for key, value in data.items():
                    if isinstance(value, dict):
                        self._facts[key] = Fact(
                            key=key,
                            value=value.get("value"),
                            category=value.get("category", "general"),
                            confidence=value.get("confidence", 1.0),
                            source=value.get("source", "session"),
                            created_at=datetime.fromisoformat(value.get("created_at", datetime.now().isoformat())),
                        )
                    else:
                        self._facts[key] = Fact(key=key, value=value)

    def _save_facts(self) -> None:
        """Save facts to disk."""
        data = {key: fact.to_dict() for key, fact in self._facts.items()}
        with open(self.facts_file, "w") as f:
            json.dump(data, f, indent=2)

    def create_session(self) -> Session:
        """Create a new session."""
        session = Session(
            id=str(uuid.uuid4())[:8],
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        self.save_session(session)
        return session

    def save_session(self, session: Session) -> None:
        """Save a session to disk."""
        session.updated_at = datetime.now()
        path = self.sessions_dir / f"{session.id}.json"
        with open(path, "w") as f:
            json.dump(session.to_dict(), f, indent=2)

    def load_session(self, session_id: str) -> Session | None:
        """Load a session from disk."""
        path = self.sessions_dir / f"{session_id}.json"
        if path.exists():
            with open(path) as f:
                return Session.from_dict(json.load(f))
        return None

    def list_sessions(self, limit: int = 20) -> list[Session]:
        """List recent sessions."""
        sessions = []
        for path in sorted(self.sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            with open(path) as f:
                sessions.append(Session.from_dict(json.load(f)))
        return sessions

    def add_fact(self, key: str, value: Any, category: str = "general", confidence: float = 1.0) -> None:
        """Add a fact to memory."""
        self._facts[key] = Fact(key=key, value=value, category=category, confidence=confidence)
        self._save_facts()

    def get_fact(self, key: str) -> Any | None:
        """Get a fact from memory."""
        return self._facts.get(key)

    def get_facts_by_category(self, category: str) -> list[Fact]:
        """Get all facts in a category."""
        return [f for f in self._facts.values() if f.category == category]

    def get_all_facts(self) -> dict[str, Any]:
        """Get all facts as a dictionary."""
        return {key: fact.value for key, fact in self._facts.items()}

    def save_todos(self, todos: list[dict[str, Any]]) -> None:
        """Save todo list."""
        with open(self.todos_file, "w") as f:
            json.dump(todos, f, indent=2)

    def load_todos(self) -> list[dict[str, Any]]:
        """Load todo list."""
        if self.todos_file.exists():
            with open(self.todos_file) as f:
                return json.load(f)
        return []

    def save_project_context(self, project_id: str, context: dict[str, Any]) -> None:
        """Save project-specific context."""
        project_dir = self.projects_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / "context.json"
        with open(path, "w") as f:
            json.dump(context, f, indent=2)

    def load_project_context(self, project_id: str) -> dict[str, Any] | None:
        """Load project-specific context."""
        path = self.projects_dir / project_id / "context.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None

    def get_context_summary(self) -> str:
        """Get a summary of all stored context for injection into prompts."""
        parts = []

        if self._facts:
            parts.append("## User Facts\n")
            for key, fact in sorted(self._facts.items()):
                parts.append(f"- {key}: {fact.value}")

        recent_sessions = self.list_sessions(limit=5)
        if recent_sessions:
            parts.append("\n## Recent Sessions\n")
            for session in recent_sessions:
                date = session.created_at.strftime("%Y-%m-%d %H:%M")
                outcome = session.outcome or "in progress"
                parts.append(f"- [{date}] {session.id}: {outcome}")

        return "\n".join(parts) if parts else "(no context stored)"


from .vectors import VectorMemory, MemoryEntry, VectorMemoryBackend, SimpleKeywordBackend

# Global memory instance
_memory: Memory | None = None


def get_memory() -> Memory:
    """Get the global memory instance."""
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory


def reset_memory() -> None:
    """Reset the global memory instance."""
    global _memory
    _memory = None
