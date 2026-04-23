"""Rollback Manager for Nexus - provides automatic backup and restore functionality."""

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class RollbackPoint:
    id: str
    timestamp: str
    description: str
    paths: dict[str, str]  # original_path -> backup_path
    checksum: str


class RollbackManager:
    """
    Creates rollback points before risky operations and restores on failure.

    Usage:
        manager = RollbackManager()

        # Before a risky operation
        point = await manager.create_point("update config", ["/path/to/file"])

        # If operation fails
        await manager.restore(point.id)
    """

    def __init__(self, backup_dir: Path | None = None):
        self.backup_dir = backup_dir or (Path.home() / ".nexus" / "rollbacks")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = self.backup_dir / "index.json"
        self._points: dict[str, RollbackPoint] = {}
        self._load_index()

    def _load_index(self) -> None:
        """Load rollback points from index."""
        if self._index_file.exists():
            try:
                with open(self._index_file) as f:
                    data = json.load(f)
                    for p in data.get("points", []):
                        self._points[p["id"]] = RollbackPoint(**p)
            except Exception:
                pass

    def _save_index(self) -> None:
        """Save rollback points to index."""
        with open(self._index_file, "w") as f:
            json.dump({"points": [asdict(p) for p in self._points.values()]}, f, indent=2)

    def _generate_id(self, description: str) -> str:
        """Generate a unique ID for a rollback point."""
        timestamp = datetime.now().isoformat()
        hash_input = f"{description}{timestamp}"
        return hashlib.sha256(hash_input.encode()).hexdigest()[:12]

    async def create_point(
        self,
        description: str,
        paths: list[str],
    ) -> RollbackPoint:
        """
        Create a rollback point for the given paths.
        Returns a RollbackPoint that can be used to restore later.
        """
        point_id = self._generate_id(description)
        timestamp = datetime.now().isoformat()
        backup_map: dict[str, str] = {}

        for path_str in paths:
            path = Path(path_str).resolve()
            if not path.exists():
                continue

            backup_name = f"{path.name}_{point_id}{path.suffix}"
            backup_path = self.backup_dir / backup_name

            if path.is_file():
                shutil.copy2(path, backup_path)
            elif path.is_dir():
                shutil.copytree(path, backup_path, dirs_exist_ok=True)

            backup_map[str(path)] = str(backup_path)

        checksum = hashlib.sha256(json.dumps(backup_map, sort_keys=True).encode()).hexdigest()

        point = RollbackPoint(
            id=point_id,
            timestamp=timestamp,
            description=description,
            paths=backup_map,
            checksum=checksum,
        )

        self._points[point_id] = point
        self._save_index()

        return point

    async def restore(self, point_id: str) -> bool:
        """
        Restore files from a rollback point.
        Returns True if successful.
        """
        point = self._points.get(point_id)
        if not point:
            return False

        for original_path, backup_path in point.paths.items():
            backup = Path(backup_path)
            original = Path(original_path)

            if not backup.exists():
                continue

            if original.exists():
                if original.is_file():
                    original.unlink()
                elif original.is_dir():
                    shutil.rmtree(original)

            if backup.is_file():
                shutil.copy2(backup, original)
            elif backup.is_dir():
                shutil.copytree(backup, original)

        return True

    async def cleanup(self, point_id: str) -> None:
        """Remove a rollback point and its backups."""
        point = self._points.pop(point_id, None)
        if not point:
            return

        for backup_path in point.paths.values():
            backup = Path(backup_path)
            if backup.exists():
                if backup.is_file():
                    backup.unlink()
                elif backup.is_dir():
                    shutil.rmtree(backup)

        self._save_index()

    def list_points(self) -> list[RollbackPoint]:
        """List all rollback points."""
        return sorted(self._points.values(), key=lambda p: p.timestamp, reverse=True)

    def get_point(self, point_id: str) -> RollbackPoint | None:
        """Get a specific rollback point."""
        return self._points.get(point_id)


_rollback_manager: RollbackManager | None = None


def get_rollback_manager() -> RollbackManager:
    """Get the global rollback manager instance."""
    global _rollback_manager
    if _rollback_manager is None:
        _rollback_manager = RollbackManager()
    return _rollback_manager
