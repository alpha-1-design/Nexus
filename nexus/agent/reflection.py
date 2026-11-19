"""Nexus Run Reflection Engine — per-run outcome journal.

The orchestrator calls :meth:`ReflectionEngine.perform_reflection` once per
completed ``run()``; each call appends ONE JSON line to
``<session_dir>/reflections.jsonl`` with the run's outcome metrics (task
snippet, ok/error, duration, tool-call count, tokens). The file is a plain
JSON-lines log that session reviews (and future lesson synthesis) can read
back with :meth:`ReflectionEngine.load_reflections`.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ReflectionEngine:
    """Records one reflection entry (JSON line) per completed run."""

    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)
        self.reflections_file = self.session_dir / "reflections.jsonl"

    def perform_reflection(self, turn: Any) -> dict[str, Any] | None:
        """Append a reflection entry for a completed run.

        Extracts outcome metrics from the turn and appends one JSON line to
        ``<session_dir>/reflections.jsonl``. Never raises — reflection is
        best-effort and must never break a turn that otherwise succeeded.
        Returns the entry, or None when recording failed.
        """
        try:
            error = getattr(turn, "error", None)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "task": (getattr(turn, "user_message", "") or "")[:120],
                "outcome": "error" if error else "ok",
                "error": error,
                "duration_ms": getattr(turn, "duration_ms", 0) or 0,
                "tool_calls": len(getattr(turn, "tool_calls", None) or []),
                "tokens_used": getattr(turn, "tokens_used", 0) or 0,
            }
            self.session_dir.mkdir(parents=True, exist_ok=True)
            with open(self.reflections_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return entry
        except Exception:
            return None

    def load_reflections(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read back the last ``limit`` entries (oldest first). Never raises."""
        try:
            lines = self.reflections_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate a torn/corrupt line — skip it
        return entries
