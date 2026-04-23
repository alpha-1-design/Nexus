import dataclasses
from datetime import datetime


@dataclasses.dataclass
class ProjectTask:
    id: int
    title: str
    status: str = "pending"  # pending, in_progress, completed
    description: str = ""
    created_at: datetime = dataclasses.field(default_factory=datetime.now)


class TaskTracker:
    """Manages the list of current objectives for a session."""

    def __init__(self):
        self.tasks: list[ProjectTask] = []
        self._counter = 0

    def add_task(self, title: str, description: str = "") -> int:
        self._counter += 1
        task = ProjectTask(id=self._counter, title=title, description=description)
        self.tasks.append(task)
        return task.id

    def update_status(self, task_id: int, status: str):
        for t in self.tasks:
            if t.id == task_id:
                t.status = status
                break

    def set_tasks(self, tasks_list: list[str]):
        """Reset and set a new list of tasks from a plan."""
        self.tasks = []
        self._counter = 0
        for title in tasks_list:
            self.add_task(title)

    def get_checklist(self) -> str:
        """Returns a formatted checklist of tasks."""
        if not self.tasks:
            return "No active tasks."

        lines = ["\n\033[1mCurrent Project Objectives:\033[0m"]
        for t in self.tasks:
            icon = "✓" if t.status == "completed" else "○"
            color = "\033[92m" if t.status == "completed" else "\033[90m"
            status_text = f"{color}{icon} {t.title}\033[0m"
            lines.append(f"  {status_text}")
        return "\n".join(lines) + "\n"
