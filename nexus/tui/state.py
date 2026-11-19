"""TUI State Management.

The state manager is the bridge between the orchestrator's callbacks and the
Textual widgets. Widgets never touch orchestrator internals; they read this
state and the app relayouts them when it changes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any


class MessageRole(Enum):
    USER = auto()
    ASSISTANT = auto()
    SYSTEM = auto()
    TOOL = auto()


@dataclass
class ChatMessage:
    """A chat message.

    Doubles as the payload for a tool-call card when `tool_name` is set:
    the transcript renders those as collapsible cards instead of prose.
    """

    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_name: str | None = None
    # --- tool-call card fields (used when tool_name is set) ---
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_status: str | None = None  # "running" | "done" | "error"
    tool_result: str | None = None
    tool_error: str | None = None
    tool_duration_ms: int | None = None
    tool_diff: str | None = None  # unified diff for edit/write tools
    card_id: str | None = None
    # True while assistant tokens are still streaming in
    streaming: bool = False

    @property
    def is_tool_card(self) -> bool:
        return self.tool_name is not None


class ToolStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    DONE = auto()
    ERROR = auto()


@dataclass
class ToolInfo:
    """Information about a tool execution."""

    name: str
    status: ToolStatus = ToolStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    duration_ms: int = 0


class AgentStatus(Enum):
    IDLE = auto()
    THINKING = auto()
    TOOL_USE = auto()
    WAITING = auto()
    ERROR = auto()


@dataclass
class AgentInfo:
    """Information about an active agent."""

    name: str
    role: str
    status: AgentStatus = AgentStatus.IDLE
    model: str | None = None
    current_task: str | None = None


@dataclass
class ThinkingStep:
    """A thinking step (mirrors the engine's step for display)."""

    step_number: int
    description: str
    details: str = ""
    expanded: bool = True
    # engine step id, so update events can find the block after mount
    step_id: str | None = None
    # "running" | "done" | "error"
    status: str = "done"
    duration_ms: int | None = None


@dataclass
class TUIState:
    """State for the TUI."""

    messages: list[ChatMessage] = field(default_factory=list)
    thinking_steps: list[ThinkingStep] = field(default_factory=list)
    active_agents: list[AgentInfo] = field(default_factory=list)
    tool_statuses: dict[str, ToolInfo] = field(default_factory=dict)
    session_id: str | None = None
    active_model: str | None = None
    is_busy: bool = False
    error: str | None = None
    # --- live token accounting for the status bar ---
    total_tokens: int = 0
    context_tokens: int = 0
    max_context_tokens: int = 128000
    last_turn_ms: int = 0


class TUIStateManager:
    """Manages state for the TUI and coordinates between components."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._state = TUIState()
        self._listeners: list[callable] = []
        self._initialized = True

    @property
    def state(self) -> TUIState:
        return self._state

    def reset(self) -> None:
        self._state = TUIState()

    def add_message(self, role: MessageRole, content: str, tool_calls: list[dict] | None = None, tool_name: str | None = None) -> None:
        msg = ChatMessage(
            role=role,
            content=content,
            tool_calls=tool_calls or [],
            tool_name=tool_name,
        )
        self._state.messages.append(msg)
        self._notify()

    def record_message(self, msg: ChatMessage) -> None:
        """Append to history WITHOUT notifying listeners.

        The chat panel renders messages directly (so streaming can update a
        single widget cheaply); this is only the session history mirror.
        """
        self._state.messages.append(msg)

    def add_thinking_step(self, step_number: int, description: str, details: str = "", step_id: str | None = None) -> ThinkingStep:
        step = ThinkingStep(
            step_number=step_number,
            description=description,
            details=details,
            status="running",
            step_id=step_id,
        )
        self._state.thinking_steps.append(step)
        self._notify()
        return step

    def update_thinking_step(self, step_id: str, status: str, details: str | None = None, duration_ms: int | None = None) -> None:
        """Update a step that was already shown (engine 'finish' events)."""
        for step in self._state.thinking_steps:
            if step.step_id == step_id:
                step.status = status
                if details is not None:
                    step.details = details
                if duration_ms is not None:
                    step.duration_ms = duration_ms
                break
        self._notify()

    def clear_thinking(self) -> None:
        self._state.thinking_steps = []
        self._notify()

    def add_agent(self, name: str, role: str, model: str | None = None) -> None:
        # Idempotent: the orchestrator agent is re-registered every turn.
        for agent in self._state.active_agents:
            if agent.name == name:
                agent.role = role
                if model:
                    agent.model = model
                self._notify()
                return
        self._state.active_agents.append(AgentInfo(name=name, role=role, model=model))
        self._notify()

    def update_agent_status(self, name: str, status: AgentStatus, task: str | None = None) -> None:
        for agent in self._state.active_agents:
            if agent.name == name:
                agent.status = status
                agent.current_task = task
                break
        self._notify()

    def remove_agent(self, name: str) -> None:
        self._state.active_agents = [a for a in self._state.active_agents if a.name != name]
        self._notify()

    def start_tool(self, tool_name: str) -> None:
        self._state.tool_statuses[tool_name] = ToolInfo(
            name=tool_name,
            status=ToolStatus.RUNNING,
            started_at=datetime.now(),
        )
        self._notify()

    def finish_tool(self, tool_name: str, result: str | None = None, error: str | None = None) -> None:
        if tool_name in self._state.tool_statuses:
            tool = self._state.tool_statuses[tool_name]
            tool.status = ToolStatus.DONE if not error else ToolStatus.ERROR
            tool.finished_at = datetime.now()
            tool.result = result
            tool.error = error
            if tool.started_at and tool.finished_at:
                tool.duration_ms = int((tool.finished_at - tool.started_at).total_seconds() * 1000)
        self._notify()

    def set_turn_stats(self, tokens_used: int, duration_ms: int = 0, max_context_tokens: int | None = None) -> None:
        """Feed the status bar after a turn: cumulative tokens + how full the
        context window is (the orchestrator already estimates tokens)."""
        state = self._state
        state.total_tokens += max(0, tokens_used)
        state.context_tokens = max(0, tokens_used)
        if max_context_tokens:
            state.max_context_tokens = max_context_tokens
        if duration_ms:
            state.last_turn_ms = duration_ms
        self._notify()

    def set_busy(self, busy: bool) -> None:
        self._state.is_busy = busy
        self._notify()

    def set_error(self, error: str | None) -> None:
        self._state.error = error
        self._notify()

    def set_session(self, session_id: str) -> None:
        self._state.session_id = session_id
        self._notify()

    def set_active_model(self, model: str) -> None:
        self._state.active_model = model
        self._notify()

    def subscribe(self, listener: callable) -> None:
        self._listeners.append(listener)

    def refresh(self) -> None:
        """Re-notify listeners (used after mutating state in place)."""
        self._notify()

    def _notify(self) -> None:
        for listener in self._listeners:
            listener(self._state)


def get_state_manager() -> TUIStateManager:
    """Get the global state manager instance."""
    return TUIStateManager()
