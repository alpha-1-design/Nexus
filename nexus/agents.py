"""Multi-agent system for Nexus.

Enables automatic spawning of sub-agents (planner, coder, reviewer, tester, researcher)
that collaborate on tasks together in a shared team chat.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable
import asyncio
import uuid
import itertools

_team_counter = itertools.count(1)
_agent_counter = itertools.count(1)
_msg_counter = itertools.count(1)

def _next_team_id() -> str:
    return str(next(_team_counter))

def _next_agent_id() -> str:
    return str(next(_agent_counter))

def _next_msg_id() -> str:
    return str(next(_msg_counter))

class AgentRole(Enum):
    LEAD = "lead"           # Main Nexus agent, coordinates
    PLANNER = "planner"     # Breaks down tasks
    CODER = "coder"         # Writes code
    REVIEWER = "reviewer"   # Reviews and spots issues
    TESTER = "tester"       # Writes and runs tests
    RESEARCHER = "researcher" # Web search, docs lookup

class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETE = "complete"
    ERROR = "error"

@dataclass
class Agent:
    agent_id: str
    name: str
    role: AgentRole
    status: AgentStatus = AgentStatus.IDLE
    model: str = "default"
    provider: str = "default"
    messages: list[dict] = field(default_factory=list)
    spawned_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    color: str = "green"  # For terminal/TUI display

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "role": self.role.value,
            "status": self.status.value,
            "model": self.model,
            "provider": self.provider,
            "message_count": len(self.messages),
            "spawned_at": self.spawned_at.isoformat(),
        }

@dataclass
class TeamMessage:
    msg_id: str
    agent_id: str
    agent_name: str
    content: str
    role_in_team: str = "member"  # "lead" or "member"
    timestamp: datetime = field(default_factory=datetime.now)
    msg_type: str = "message"  # "message", "tool_call", "tool_result", "system"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "content": self.content,
            "role_in_team": self.role_in_team,
            "timestamp": self.timestamp.isoformat(),
            "type": self.msg_type,
        }

class MultiAgentTeam:
    """Manages a team of agents working on tasks together."""

    ROLE_PROMPTS = {
        AgentRole.PLANNER: """You are a PLANNER agent. Your job is to break down complex tasks into clear, executable steps. 
Be specific about what tools to use and what the expected outcome is. 
When collaborating with other agents, be direct and concise. 
Use @agentname to mention specific agents.""",
        
AgentRole.CODER: """You are a CODER agent. Your job is to write clean, efficient code.
        Read existing code before modifying. Make small, focused changes.
        When stuck, ask the planner for clarification. When done, notify the reviewer.
        Use @planner to ask questions. Use @reviewer to request review.""",
        
        AgentRole.REVIEWER: """You are a REVIEWER agent. Your job is to review code changes for bugs, 
        security issues, style problems, and best practices.
        Be thorough but constructive. Point out specific issues with line numbers.
        When you approve, say so clearly. Use @coder to request fixes.""",
        
        AgentRole.TESTER: """You are a TESTER agent. Your job is to write comprehensive tests.
        Cover happy paths and edge cases. Run tests and report results clearly.
        Use @coder to request test data or fixtures. Use @reviewer if you find bugs.""",
        
        AgentRole.RESEARCHER: """You are a RESEARCHER agent. Your job is to find information,
        documentation, code examples, and best practices online.
        Cite your sources. Summarize findings concisely.
        Use @planner or @coder to share relevant findings.""",
    }

    ROLE_COLORS = {
        AgentRole.PLANNER: "blue",
        AgentRole.CODER: "green",
        AgentRole.REVIEWER: "yellow",
        AgentRole.TESTER: "magenta",
        AgentRole.RESEARCHER: "cyan",
        AgentRole.LEAD: "white bold",
    }

    def __init__(self, lead_name: str = "nexus", provider_manager=None):
        self.team_id = _next_team_id()
        self.lead_name = lead_name
        self.pm = provider_manager
        self.agents: dict[str, Agent] = {}
        self.team_messages: list[TeamMessage] = []
        self._callbacks: list[Callable] = []
        self._lock = asyncio.Lock()
        self._agent_counter = 0
        self.lead_name = lead_name
        self.pm = provider_manager
        self.agents: dict[str, Agent] = {}
        self.team_messages: list[TeamMessage] = []
        self._callbacks: list[Callable] = []
        self._lock = asyncio.Lock()
        self._agent_counter = 0
    
    def spawn(self, role: AgentRole, name: str | None = None, 
              task: str | None = None, model: str | None = None) -> Agent:
        """Spawn a new agent with the given role."""
        self._agent_counter += 1
        agent_id = str(self._agent_counter)
        agent_name = name or f"{role.value}-{agent_id}"
        
        agent = Agent(
            agent_id=agent_id,
            name=agent_name,
            role=role,
            model=model or "default",
            color=self.ROLE_COLORS.get(role, "green"),
            status=AgentStatus.RUNNING,
        )
        
        self.agents[agent_id] = agent
        
        self._broadcast_system(f"Spawned: {agent_name} ({role.value})")
        
        if task:
            asyncio.create_task(self._agent_task(agent, task))
        
        return agent

    async def _agent_task(self, agent: Agent, task: str) -> None:
        """Run a task for an agent."""
        from ..providers import get_manager
        
        pm = self.pm or get_manager()
        system_prompt = self.ROLE_PROMPTS.get(agent.role, "")
        
        context = self._build_team_context(agent)
        messages = [
            {"role": "system", "content": system_prompt + "\n\n" + context},
            {"role": "user", "content": task},
        ]
        
        self._broadcast(agent.agent_id, agent.name, 
                       f"Starting task: {task}", "system")
        
        try:
            response = await pm.complete(messages=messages)
            content = response.content if hasattr(response, "content") else str(response)
            
            self._broadcast(agent.agent_id, agent.name, content, "message")
            agent.status = AgentStatus.COMPLETE
            agent.messages.append({"role": "assistant", "content": content})
            
            self._broadcast_system(f"✅ {agent.name} complete")
            
        except Exception as e:
            agent.status = AgentStatus.ERROR
            self._broadcast_system(f"✗ {agent.name} error: {e}")

    def _build_team_context(self, agent: Agent) -> str:
        """Build context about the team for an agent."""
        teammates = [a for a in self.agents.values() if a.agent_id != agent.agent_id]
        context = "\n\n## Your Team\n"
        context += f"Your name: {agent.name} (role: {agent.role.value})\n"
        if teammates:
            context += "Other team members:\n"
            for t in teammates:
                context += f"  - @{t.name} ({t.role.value}, status: {t.status.value})\n"
        context += "\n## Team Rules\n"
        context += "- Use @agentname to mention team members\n"
        context += "- Be concise and specific\n"
        context += "- Report completion or issues clearly\n"
        return context

    def _broadcast(self, agent_id: str, agent_name: str, content: str, 
                   msg_type: str = "message") -> None:
        """Broadcast a message to the team."""
        msg = TeamMessage(
            msg_id=str(uuid.uuid4())[:8],
            agent_id=agent_id,
            agent_name=agent_name,
            content=content,
            msg_type=msg_type,
        )
        self.team_messages.append(msg)
        for cb in self._callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    def _broadcast_system(self, content: str) -> None:
        """Broadcast a system message."""
        self._broadcast("system", "NEXUS", content, "system")

    def mention(self, from_agent: str, to_name: str, message: str) -> None:
        """One agent mentions another."""
        target = next((a for a in self.agents.values() if a.name == to_name), None)
        if target:
            target.status = AgentStatus.WAITING
            self._broadcast(from_agent, self.agents.get(from_agent, Agent("", "", AgentRole.LEAD)).name,
                           f"@{to_name} {message}", "message")

    def on_message(self, callback: Callable) -> None:
        """Register a callback for team messages."""
        self._callbacks.append(callback)

    def list_agents(self) -> list[Agent]:
        return list(self.agents.values())

    def get_messages(self, agent_id: str | None = None, 
                     limit: int = 100) -> list[TeamMessage]:
        """Get team messages, optionally filtered by agent."""
        msgs = self.team_messages[-limit:]
        if agent_id:
            msgs = [m for m in msgs if m.agent_id == agent_id]
        return msgs

    def format_chat(self, limit: int = 50) -> str:
        """Format team chat as readable text."""
        lines = []
        for msg in self.team_messages[-limit:]:
            timestamp = msg.timestamp.strftime("%H:%M")
            if msg.msg_type == "system":
                lines.append(f"[{timestamp}] NEXUS: {msg.content}")
            else:
                lines.append(f"[{timestamp}] {msg.agent_name}: {msg.content}")
        return "\n".join(lines)

    def kill(self, agent_id: str) -> bool:
        """Kill an agent."""
        agent = self.agents.get(agent_id)
        if agent and agent.role != AgentRole.LEAD:
            agent.status = AgentStatus.IDLE
            self._broadcast_system(f"⬇️ Terminated: {agent.name}")
            return True
        return False

    def auto_spawn_for_task(self, task: str) -> list[Agent]:
        """Automatically spawn agents based on task complexity."""
        spawned = []
        task_lower = task.lower()
        
        # Always spawn coder for coding tasks
        if any(w in task_lower for w in ["build", "create", "write", "add", "implement", "fix", "update"]):
            spawned.append(self.spawn(AgentRole.CODER))
        
        # Spawn planner for complex tasks
        if any(w in task_lower for w in ["complex", "multiple", "entire", "architecture", "system", "migrate"]):
            spawned.append(self.spawn(AgentRole.PLANNER))
        
        # Spawn reviewer for edits
        if any(w in task_lower for w in ["review", "check", "audit", "security"]):
            spawned.append(self.spawn(AgentRole.REVIEWER))
        
        # Spawn tester for new code
        if any(w in task_lower for w in ["test", "coverage", "unit test"]):
            spawned.append(self.spawn(AgentRole.TESTER))
        
        # Spawn researcher for docs/web tasks
        if any(w in task_lower for w in ["research", "find", "search", "look up", "documentation"]):
            spawned.append(self.spawn(AgentRole.RESEARCHER))
        
        return spawned


# Global team
_team: MultiAgentTeam | None = None

def get_team() -> MultiAgentTeam | None:
    return _team

def init_team(lead_name: str = "nexus", pm=None) -> MultiAgentTeam:
    global _team
    _team = MultiAgentTeam(lead_name=lead_name, provider_manager=pm)
    return _team