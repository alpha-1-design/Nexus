"""Multi-agent system for Nexus.

Enables automatic spawning of sub-agents (planner, coder, reviewer, tester, researcher)
that collaborate on tasks together in a shared team chat.
"""

<<<<<<< HEAD
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
=======
import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any


_team_counter = 0
_msg_counter = 0


def _next_team_id() -> str:
    global _team_counter
    _team_counter += 1
    return f"team-{_team_counter}"


def _next_msg_id() -> str:
    global _msg_counter
    _msg_counter += 1
    return f"m{_msg_counter}"


class AgentRole(Enum):
    LEAD = "lead"
    PLANNER = "planner"
    CODER = "coder"
    REVIEWER = "reviewer"
    TESTER = "tester"
    RESEARCHER = "researcher"

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)

class AgentStatus(Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETE = "complete"
    ERROR = "error"

<<<<<<< HEAD
=======

_agent_counter = 0


def _next_agent_id() -> str:
    global _agent_counter
    _agent_counter += 1
    return f"nx-{_agent_counter:03d}"


@dataclass
class AgentTemplate:
    """A pre-configured agent team profile."""

    name: str
    display_name: str
    description: str
    agents: list[tuple[AgentRole, str]]  # (Role, Specialized Prompt)
    workflow: list[str]  # Steps for the lead agent to coordinate


class TemplateManager:
    """Handles loading and saving agent templates."""

    def __init__(self, config_dir: Path):
        self.templates_file = config_dir / "templates.json"
        self.templates: dict[str, AgentTemplate] = self._load_defaults()

    def _load_defaults(self) -> dict[str, AgentTemplate]:
        return {
            "feature-architect": AgentTemplate(
                name="feature-architect",
                display_name="Feature Architect",
                description="Full lifecycle: Planning -> Coding -> Reviewing",
                agents=[
                    (AgentRole.PLANNER, "Focus on architectural soundness and modularity."),
                    (AgentRole.CODER, "Implement the plan with high precision and clean code."),
                    (AgentRole.REVIEWER, "Audit for edge cases and performance bottlenecks."),
                ],
                workflow=[
                    "1. Define specs",
                    "2. Implement core logic",
                    "3. Peer review",
                    "4. Final polish",
                ],
            ),
            "security-auditor": AgentTemplate(
                name="security-auditor",
                display_name="Security Auditor",
                description="Deep dive into vulnerability research and patching",
                agents=[
                    (
                        AgentRole.RESEARCHER,
                        "Find known CVEs and common attack patterns for this stack.",
                    ),
                    (
                        AgentRole.REVIEWER,
                        "Perform a critical security audit of the current implementation.",
                    ),
                    (AgentRole.CODER, "Implement security patches and hardening measures."),
                ],
                workflow=[
                    "1. Research attack vectors",
                    "2. Audit codebase",
                    "3. Implement fixes",
                    "4. Verify patches",
                ],
            ),
            "bug-hunter": AgentTemplate(
                name="bug-hunter",
                display_name="Bug Hunter",
                description="Rapid reproduction and resolution of defects",
                agents=[
                    (AgentRole.TESTER, "Create a minimal reproduction case for the bug."),
                    (AgentRole.RESEARCHER, "Analyze logs and trace the root cause."),
                    (AgentRole.CODER, "Fix the bug without introducing regressions."),
                ],
                workflow=[
                    "1. Reproduce bug",
                    "2. Root cause analysis",
                    "3. Implement fix",
                    "4. Regression test",
                ],
            ),
            "docs-expert": AgentTemplate(
                name="docs-expert",
                display_name="Documentation Expert",
                description="Comprehensive technical documentation and API guides",
                agents=[
                    (AgentRole.RESEARCHER, "Extract all key API endpoints and logic flows."),
                    (AgentRole.CODER, "Write clear, concise documentation and examples."),
                ],
                workflow=[
                    "1. Analyze API",
                    "2. Draft documentation",
                    "3. Verify examples",
                    "4. Final review",
                ],
            ),
        }

    def load(self) -> None:
        if self.templates_file.exists():
            try:
                with open(self.templates_file) as f:
                    data = json.load(f)
                    for name, d in data.items():
                        # Convert role names back to AgentRole enum
                        agents = [(AgentRole(r), p) for r, p in d["agents"]]
                        self.templates[name] = AgentTemplate(
                            name=name,
                            display_name=d["display_name"],
                            description=d["description"],
                            agents=agents,
                            workflow=d["workflow"],
                        )
            except Exception:
                pass

    def save(self) -> None:
        self.templates_file.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for name, t in self.templates.items():
            data[name] = {
                "display_name": t.display_name,
                "description": t.description,
                "agents": [(r.value, p) for r, p in t.agents],
                "workflow": t.workflow,
            }
        with open(self.templates_file, "w") as f:
            json.dump(data, f, indent=2)


>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
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

<<<<<<< HEAD
=======

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
@dataclass
class TeamMessage:
    msg_id: str
    agent_id: str
    agent_name: str
    content: str
    role_in_team: str = "member"  # "lead" or "member"
    timestamp: datetime = field(default_factory=datetime.now)
    msg_type: str = "message"  # "message", "tool_call", "tool_result", "system"
<<<<<<< HEAD
    
=======

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
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

<<<<<<< HEAD
=======

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
class MultiAgentTeam:
    """Manages a team of agents working on tasks together."""

    ROLE_PROMPTS = {
<<<<<<< HEAD
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
=======
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
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
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
<<<<<<< HEAD
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
        
=======

    @property
    def lead(self) -> Agent | None:
        return next((a for a in self.agents.values() if a.role == AgentRole.LEAD), None)

    def spawn(
        self,
        role: AgentRole,
        name: str | None = None,
        task: str | None = None,
        model: str | None = None,
        specialized_prompt: str | None = None,
    ) -> Agent:
        """Spawn a new agent with the given role."""
        agent_id = _next_agent_id()
        agent_name = name or f"{role.value}/{agent_id}"

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
        agent = Agent(
            agent_id=agent_id,
            name=agent_name,
            role=role,
            model=model or "default",
            color=self.ROLE_COLORS.get(role, "green"),
            status=AgentStatus.RUNNING,
        )
<<<<<<< HEAD
        
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
=======

        self.agents[agent_id] = agent

        role_prompt = self.ROLE_PROMPTS.get(role, "")
        final_prompt = (
            f"{role_prompt}\n\nSpecialization: {specialized_prompt}"
            if specialized_prompt
            else role_prompt
        )

        self._broadcast_system(f"[spawn] {agent_name} started")

        if task:
            asyncio.create_task(self._agent_task(agent, task, system_prompt_override=final_prompt))

        return agent

    async def _agent_task(
        self, agent: Agent, task: str, system_prompt_override: str | None = None
    ) -> None:
        """Run a task for an agent."""
        from nexus.providers import Message, get_manager

        pm = self.pm or get_manager()
        system_prompt = system_prompt_override or self.ROLE_PROMPTS.get(agent.role, "")

        context = self._build_team_context(agent)
        messages = [
            Message(role="system", content=system_prompt + "\n\n" + context),
            Message(role="user", content=task),
        ]

        self._broadcast(agent.agent_id, agent.name, f"Starting: {task}", "system")

        try:
            response = await pm.complete(messages=messages)
            content = response.content if hasattr(response, "content") else str(response)

            self._broadcast(agent.agent_id, agent.name, content, "message")
            agent.status = AgentStatus.COMPLETE
            agent.messages.append({"role": "assistant", "content": content})

            self._broadcast_system(f"[done] {agent.name} finished")

        except Exception as e:
            agent.status = AgentStatus.ERROR
            self._broadcast_system(f"[fail] {agent.name}: {e}")
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)

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

<<<<<<< HEAD
    def _broadcast(self, agent_id: str, agent_name: str, content: str, 
                   msg_type: str = "message") -> None:
        """Broadcast a message to the team."""
        msg = TeamMessage(
            msg_id=str(uuid.uuid4())[:8],
=======
    def _broadcast(
        self, agent_id: str, agent_name: str, content: str, msg_type: str = "message"
    ) -> None:
        """Broadcast a message to the team."""
        msg = TeamMessage(
            msg_id=_next_msg_id(),
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
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
<<<<<<< HEAD
            self._broadcast(from_agent, self.agents.get(from_agent, Agent("", "", AgentRole.LEAD)).name,
                           f"@{to_name} {message}", "message")
=======
            self._broadcast(
                from_agent,
                self.agents.get(from_agent, Agent("", "", AgentRole.LEAD)).name,
                f"@{to_name} {message}",
                "message",
            )
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)

    def on_message(self, callback: Callable) -> None:
        """Register a callback for team messages."""
        self._callbacks.append(callback)

    def list_agents(self) -> list[Agent]:
        return list(self.agents.values())

<<<<<<< HEAD
    def get_messages(self, agent_id: str | None = None, 
                     limit: int = 100) -> list[TeamMessage]:
=======
    def get_messages(self, agent_id: str | None = None, limit: int = 100) -> list[TeamMessage]:
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
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

<<<<<<< HEAD
=======
    def clear_agents(self) -> None:
        """Clear all agents from the team."""
        self.agents.clear()

    def spawn_template(self, template_name: str, config_dir: Path) -> bool:
        """Spawn a team based on a template."""
        tm = TemplateManager(config_dir)
        tm.load()

        template = tm.templates.get(template_name)
        if not template:
            return False

            self._broadcast_system(f"[load] Template: {template.display_name}")
            self._broadcast_system(f"Description: {template.description}")

        # Spawn specified agents
        for role, spec_prompt in template.agents:
            self.spawn(
                role,
                specialized_prompt=spec_prompt,
                task=f"Initialized via {template.name} template.",
            )

        # Broadcast workflow
        workflow_msg = "Proposed Workflow:\n" + "\n".join(template.workflow)
        self._broadcast_system(workflow_msg)

        return True

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
    def kill(self, agent_id: str) -> bool:
        """Kill an agent."""
        agent = self.agents.get(agent_id)
        if agent and agent.role != AgentRole.LEAD:
            agent.status = AgentStatus.IDLE
<<<<<<< HEAD
            self._broadcast_system(f"⬇️ Terminated: {agent.name}")
=======
            self._broadcast_system(f"[kill] {agent.name} terminated")
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
            return True
        return False

    def auto_spawn_for_task(self, task: str) -> list[Agent]:
        """Automatically spawn agents based on task complexity."""
        spawned = []
        task_lower = task.lower()
<<<<<<< HEAD
        
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
        
=======

        if any(
            w in task_lower
            for w in ["build", "create", "write", "add", "implement", "fix", "update"]
        ):
            spawned.append(self.spawn(AgentRole.CODER, task=task))

        if any(
            w in task_lower
            for w in ["complex", "multiple", "entire", "architecture", "system", "migrate"]
        ):
            spawned.append(self.spawn(AgentRole.PLANNER, task=task))

        if any(w in task_lower for w in ["review", "check", "audit", "security"]):
            spawned.append(self.spawn(AgentRole.REVIEWER, task=task))

        if any(w in task_lower for w in ["test", "coverage", "unit test"]):
            spawned.append(self.spawn(AgentRole.TESTER, task=task))

        if any(w in task_lower for w in ["research", "find", "search", "look up", "documentation"]):
            spawned.append(self.spawn(AgentRole.RESEARCHER, task=task))

>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
        return spawned


# Global team
_team: MultiAgentTeam | None = None

<<<<<<< HEAD
def get_team() -> MultiAgentTeam | None:
    return _team

def init_team(lead_name: str = "nexus", pm=None) -> MultiAgentTeam:
    global _team
    _team = MultiAgentTeam(lead_name=lead_name, provider_manager=pm)
    return _team
=======

def get_team() -> MultiAgentTeam | None:
    return _team


def init_team(lead_name: str = "nexus", pm=None) -> MultiAgentTeam:
    global _team
    _team = MultiAgentTeam(lead_name=lead_name, provider_manager=pm)
    return _team
>>>>>>> 8b77f00 (feat: implement dynamic ReAct loop and enhance CLI/TUI)
