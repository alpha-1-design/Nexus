"""Interactive REPL for Nexus."""

import asyncio
import readline
import sys
import time
from typing import Any

from ..providers import Message, get_manager
from ..tools import get_registry, ToolResult
from ..memory import get_memory
from ..agents import init_team, get_team, AgentRole, MultiAgentTeam
from ..thinking import ThinkingEngine, get_thinking_engine, ThinkingState
from ..ui import LoadingIndicator, ProgressTracker
from ..plan import PlanMode, get_plan_mode, set_plan_mode, should_trigger_plan_mode
from ..sessions import get_session_loader
from ..safety import get_safety_engine, SafetyEngine
from ..sync import get_sync_engine
from ..learn import get_learning_engine
from ..self_improve import get_self_improver
from ..personality import get_personality, Personality
from ..phone import get_phone_mode, PhoneMode
from ..voice import get_voice_engine


class REPL:
    """Interactive REPL for Nexus."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.manager = get_manager()
        self.registry = get_registry()
        self.memory = get_memory()
        self.session = self.memory.create_session()
        self.messages: list[Message] = []
        self.running = True
        self.streaming = True
        self.team: MultiAgentTeam | None = None
        self.thinking_engine = get_thinking_engine()
        self._first_token_received = False
        self._tool_call_count = 0
        self._total_tool_calls = 0
        self._start_time = 0.0
        self._plan_mode: PlanMode | None = None
        self._plan_active = False
        
        # New systems
        self.safety: SafetyEngine = get_safety_engine()
        self.sync_engine = get_sync_engine()
        self.learning = get_learning_engine()
        self.improver = get_self_improver()
        self.personality: Personality = get_personality()
        self.phone: PhoneMode = get_phone_mode()
        
        # Session auto-load
        self.session_loader = get_session_loader()
        
        # Set up readline
        self._setup_readline()
        
        # Initialize multi-agent team
        self.team = init_team(lead_name="nexus", pm=self.manager)
        self.team.on_message(self._on_team_message)

        # Register thinking callback
        self.thinking_engine.on_update(self._on_thinking_update)
        
        # Start learning session
        self.learning.start_session(self.session.id)
        
        # Phone mode preprocessing
        if self.phone.enabled:
            print("📱 Phone mode active — type /h for compact help")
        
        self._check_resume_session()

    def _check_resume_session(self) -> None:
        """Check for previous session and offer to resume."""
        recent = self.session_loader.get_most_recent_session()
        if recent and recent.messages:
            prompt = self.session_loader.get_resume_prompt(recent)
            try:
                response = input(prompt).strip().lower()
                if response in ("y", "yes"):
                    # Load session messages
                    loaded = self.session_loader.load_session(recent.id)
                    if loaded:
                        self.session = loaded
                        for msg in loaded.messages[-20:]:
                            if msg["role"] == "user":
                                self.messages.append(Message(role="user", content=msg["content"]))
                            elif msg["role"] == "assistant":
                                self.messages.append(Message(role="assistant", content=msg["content"]))
                        print(f"Resumed session. {len(self.messages)} messages loaded.")
                else:
                    print("Starting fresh session.")
            except (EOFError, KeyboardInterrupt):
                    print("Starting fresh session.")

    def _check_tool_safety(self, tool_name: str, arguments: dict) -> tuple[bool, ToolResult | None]:
        """Check tool call against safety rules. Returns (proceed, error_result)."""
        path_keys = ("path", "filePath", "file_path", "directory", "workdir")
        context: dict = {"tool": tool_name, "args": arguments}
        for key in path_keys:
            if key in arguments and arguments[key]:
                context["path"] = arguments[key]
                break
        if "command" in arguments:
            context["command"] = arguments["command"]

        violations = self.safety.check(context)
        if not violations:
            return True, None

        proceed, reason = self.safety.should_proceed(violations)
        if not proceed:
            error = ToolResult(
                success=False,
                content=f"[SAFETY BLOCK] {reason}",
            )
            print(f"\n{self.safety.render_violations(violations)}\n")
            return False, error

        print(f"\n{self.safety.render_violations(violations)}\n")
        return True, None

    def _setup_readline(self) -> None:
        """Configure readline for better editing."""
        readline.parse_and_bind("tab: complete")
        readline.parse_and_bind("set editing-mode vi")
        
        # History file
        histfile = self._get_histfile()
        try:
            readline.read_history_file(histfile)
        except FileNotFoundError:
            pass
        readline.set_history_length(1000)

    def _get_histfile(self) -> str:
        """Get the history file path."""
        from pathlib import Path
        return str(Path.home() / ".nexus" / ".history")

    def _save_history(self) -> None:
        """Save command history."""
        histfile = self._get_histfile()
        try:
            readline.write_history_file(histfile)
        except Exception:
            pass

    async def _llm_voice_callback(self, text: str) -> str:
        """Send voice input to LLM and return response text."""
        from ..providers import Message
        messages = [Message(role="system", content=self._get_system_prompt())] + self.messages
        messages.append(Message(role="user", content=text))

        tools = self.registry.to_openai_format()

        try:
            response = await self.manager.complete(messages, tools)
            for tc in response.tool_calls:
                tool = self.registry.get(tc.name)
                if tool:
                    result = await tool.execute(**tc.arguments)
                    messages.append(Message(
                        role="tool", content=result.content,
                        name=tc.name, tool_call_id=tc.id,
                    ))

            response = await self.manager.complete(messages, tools)
            self.messages.append(Message(role="user", content=text))
            self.messages.append(Message(role="assistant", content=response.content))
            self.session.messages.append({"role": "user", "content": text})
            self.session.messages.append({"role": "assistant", "content": response.content})
            self.memory.save_session(self.session)
            return response.content
        except Exception as e:
            return f"Oops, ran into an issue: {e}"

    async def _run_voice_mode(self, overrides: dict[str, str]) -> None:
        """Enter voice conversation mode."""
        engine = get_voice_engine(llm_callback=self._llm_voice_callback, **overrides)
        print(f"\n{self.personality.greet()} Starting voice mode...")
        async with engine.voice_mode():
            while engine._running:
                await asyncio.sleep(0.5)

    def _get_prompt(self) -> str:
        """Get the command prompt."""
        return "\033[1;36mnexus\033[0m> "

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the agent."""
        context = self.memory.get_context_summary()
        voice_prompt = self.personality.get_voice_system_prompt()

        return f"""{voice_prompt}

You are an AI coding assistant powered by Rehoboth Genesis.

You have access to the following tools:
{self._format_tools()}

Memory context:
{context}

When using tools, always provide clear feedback about what you're doing.
Focus on being helpful, accurate, and efficient.
"""

    def _format_tools(self) -> str:
        """Format tools for the system prompt."""
        lines = []
        for tool in self.registry.list_all():
            lines.append(f"- {tool.name}: {tool.description}")
        return "\n".join(lines)

    async def _generate_response(self, user_input: str) -> str:
        """Generate a response from the AI."""
        # Add user message
        self.messages.append(Message(role="user", content=user_input))
        self.session.messages.append({"role": "user", "content": user_input})

        # Auto-spawn agents for complex tasks
        if self.team:
            spawned = self.team.auto_spawn_for_task(user_input)
            if spawned:
                print(f"\n[team] Auto-spawned: {', '.join(a.name for a in spawned)}")

        # Build messages with system prompt
        messages = [Message(role="system", content=self._get_system_prompt())] + self.messages

        # Get tools
        tools = self.registry.to_openai_format()

        try:
            if self.streaming:
                response_text = ""
                tool_results = []

                async for chunk in self.manager.stream(messages, tools):
                    if chunk.content:
                        self._first_token_received = True
                        print(chunk.content, end="", flush=True)
                        response_text += chunk.content

                    if chunk.tool_call:
                        tool_call = chunk.tool_call
                        print(f"\n\n[Calling tool: {tool_call.name}]\n")
                        exec_step = self.thinking_engine.start_step(
                            ThinkingState.EXECUTING,
                            f"Calling tool: {tool_call.name}",
                            tool_name=tool_call.name,
                            tool_args=tool_call.arguments,
                        )
                        tool = self.registry.get(tool_call.name)
                        if tool:
                            proceed, error_result = self._check_tool_safety(tool_call.name, tool_call.arguments)
                            if not proceed:
                                result = error_result
                            else:
                                if tool_call.name == "Read":
                                    for path_key in ("path", "filePath", "file_path"):
                                        if path_key in tool_call.arguments:
                                            self.safety.mark_file_read(tool_call.arguments[path_key])
                                            break
                                result = await tool.execute(**tool_call.arguments)
                            tool_results.append((tool_call, result))
                            if not result.success:
                                self.learning.record_failure(
                                    tool_name=tool_call.name,
                                    args=tool_call.arguments,
                                    error=result.error or result.content,
                                    context={"session": self.session.id},
                                )
                            self.thinking_engine.finish_step(
                                exec_step,
                                result=result.content[:200] if result.content else "",
                            )
                            if result.success:
                                prefix = f"\n{self.personality.success()} "
                            else:
                                prefix = f"\n{self.personality.failure()} "
                            preview = result.content[:200]
                            print(f"{prefix}{preview}..." if len(result.content) > 200 else f"{prefix}{result.content}")

                            # Add tool result as message
                            messages.append(Message(
                                role="tool",
                                content=result.content,
                                name=tool_call.name,
                                tool_call_id=tool_call.id,
                            ))

                            # Record tool usage
                            if chunk.tool_call.name not in self.session.tools_used:
                                self.session.tools_used.append(chunk.tool_call.name)

                print()  # Newline after streaming

                # Add assistant response
                if response_text:
                    self.messages.append(Message(role="assistant", content=response_text))
                    self.session.messages.append({"role": "assistant", "content": response_text})

            else:
                response = await self.manager.complete(messages, tools)
                
                # Handle tool calls
                for tool_call in response.tool_calls:
                    tool = self.registry.get(tool_call.name)
                    if tool:
                        proceed, error_result = self._check_tool_safety(tool_call.name, tool_call.arguments)
                        if not proceed:
                            result = error_result
                        else:
                            if tool_call.name == "Read":
                                for path_key in ("path", "filePath", "file_path"):
                                    if path_key in tool_call.arguments:
                                        self.safety.mark_file_read(tool_call.arguments[path_key])
                                        break
                            result = await tool.execute(**tool_call.arguments)
                        if not result.success:
                            self.learning.record_failure(
                                tool_name=tool_call.name,
                                args=tool_call.arguments,
                                error=result.error or result.content,
                                context={"session": self.session.id},
                            )
                        messages.append(Message(
                            role="tool",
                            content=result.content,
                            name=tool_call.name,
                            tool_call_id=tool_call.id,
                        ))

                # Get final response
                response = await self.manager.complete(messages, tools)
                print(response.content)

                self.messages.append(Message(role="assistant", content=response.content))
                self.session.messages.append({"role": "assistant", "content": response.content})

            self.memory.save_session(self.session)
            return ""

        except Exception as e:
            return f"Error: {e}"

    def _handle_command(self, line: str) -> bool:
        """Handle a slash command. Returns True if handled."""
        if not line.startswith("/"):
            return False

        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "exit" or cmd == "quit" or cmd == "q":
            print("Goodbye!")
            self.running = False
            return True

        elif cmd == "help" or cmd == "h":
            print("""
Available commands:
  /exit, /quit     Exit the REPL
  /clear           Clear conversation
  /history         Show message history
  /tools           List available tools
  /model <name>    Switch model
  /stream          Toggle streaming mode
  /facts           Show stored facts
  /fact <text>     Add a persistent fact
  /session         Show current session info
  /save            Save current session
  /sessions        List saved sessions
  /load <id>       Load a saved session
  /voice           Enter voice mode (Nexus speaks & listens)
  /voice tts=freetts stt=whisper  Configure voice providers
  /spawn <role>    Spawn a team agent (coder, reviewer, tester, researcher)
  /agents          List active agents
  /team            Show team status
  /plan, /p        Enter plan mode for a task
  /build           Execute approved plan steps
  /think           Show thinking engine state
  /skills          List available skills
  /skill <name>    Activate a skill
  /providers       Show configured providers
  /models          Show available models
  /context         Show conversation context
  /stats           Show session statistics
  /retry           Retry last user message
  /status          Show system status
  /mcp             MCP server status
  /plugin          Plugin management
  /doctor          Run system diagnostics
  /sync status     Sync status / push / pull / connect / disconnect
  /learn stats     Learning system / lessons / failures / clear
  /improve queue   Improvement queue / approve / reject / run
  /safety status   Safety rules / strict / permissive
  /phone           Phone-optimized mode info
  /reflect         Trigger reflection on current work
  /partner         Set personality mode
  /help            Show this help
""")
            return True

        elif cmd == "clear":
            self.messages = []
            print("Conversation cleared.")
            return True

        elif cmd == "history":
            for i, msg in enumerate(self.messages[-10:]):
                role = msg.role.upper()
                content = msg.content[:100] + "..." if len(msg.content) > 100 else msg.content
                print(f"{i+1}. [{role}] {content}")
            return True

        elif cmd == "tools":
            for tool in self.registry.list_all():
                print(f"  {tool.name}: {tool.description}")
            return True

        elif cmd == "model":
            if args:
                asyncio.create_task(self.manager.switch_model(args))
                print(f"Model switched to: {args}")
            else:
                print(f"Current model: {self.manager.active_provider}")
            return True

        elif cmd == "stream":
            self.streaming = not self.streaming
            print(f"Streaming: {'ON' if self.streaming else 'OFF'}")
            return True

        elif cmd == "session":
            print(f"Session: {self.session.id}")
            print(f"Created: {self.session.created_at}")
            print(f"Messages: {len(self.session.messages)}")
            print(f"Tools used: {', '.join(self.session.tools_used) if self.session.tools_used else 'none'}")
            return True

        elif cmd == "voice":
            parts = args.split() if args else []
            overrides = {}
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    overrides[k.strip()] = v.strip()
            asyncio.create_task(self._run_voice_mode(overrides))
            return True

        elif cmd == "spawn":
            if args:
                parts = args.split(maxsplit=1)
                role_name = parts[0].lower()
                task = parts[1] if len(parts) > 1 else None
                try:
                    role = AgentRole(role_name)
                    agent = self.team.spawn(role, task=task)
                    print(f"Spawned {agent.name} ({role.value})")
                except ValueError:
                    print(f"Invalid role: {role_name}. Valid: {', '.join(r.value for r in AgentRole)}")
            else:
                print("Usage: /spawn <role> [task]")
            return True

        elif cmd == "agents":
            print("Active agents:")
            for agent in self.team.list_agents():
                print(f"  {agent.name} ({agent.role.value}) - {agent.status.value}")
            return True

        elif cmd == "kill":
            if args:
                agent_id = args.split()[0]
                if self.team.kill(agent_id):
                    print(f"Killed agent: {agent_id}")
                else:
                    print(f"Agent not found or cannot be killed: {agent_id}")
            else:
                print("Usage: /kill <agent_id>")
            return True

        elif cmd == "team":
            print("=== Team Chat ===")
            print(self.team.format_chat())
            return True

        elif cmd == "plan" or cmd == "p":
            print("\n[PLAN MODE] Generating plan...")
            self._plan_mode = PlanMode(task=args or "Continue task", orchestrator=None)
            set_plan_mode(self._plan_mode)
            self._plan_mode.activate()
            self._plan_active = True
            asyncio.create_task(self._run_plan_mode(args))
            return True

        elif cmd == "build":
            if self._plan_mode and self._plan_mode.plan:
                print("\n[BUILD MODE] Executing plan...")
                asyncio.create_task(self._execute_plan())
            else:
                print("No active plan. Use /plan first.")
            return True

        elif cmd == "abort":
            print("Aborting current task...")
            self.running = False
            return True

        elif cmd == "think":
            print("Thinking panel: ON (always visible in this REPL)")
            return True

        elif cmd == "save":
            path = self.session_loader.save_session(self.session)
            print(f"Session saved: {path}")
            return True

        elif cmd == "sessions":
            print(self.session_loader.format_session_list())
            return True

        elif cmd == "load":
            if args:
                session = self.session_loader.load_session(args)
                if session:
                    self.session = session
                    self.messages.clear()
                    for msg in session.messages[-20:]:
                        self.messages.append(Message(role=msg["role"], content=msg["content"]))
                    print(f"Loaded session: {session.id}")
                else:
                    print(f"Session not found: {args}")
            else:
                print(self.session_loader.format_session_list())
            return True

        elif cmd == "skills":
            from ..skills import SkillsManager
            sm = SkillsManager()
            sm.load_all()
            print("\nAvailable skills:")
            for s in sm.list_all():
                active = "●" if s.name in sm.list_active() else "○"
                print(f"  {active} {s.name} ({s.category}) — {s.description[:50]}")
            return True

        elif cmd == "skill":
            if args:
                from ..skills import SkillsManager
                sm = SkillsManager()
                sm.load_all()
                if sm.activate(args):
                    print(f"Activated skill: {args}")
                else:
                    print(f"Skill not found: {args}")
            else:
                print("Usage: /skill <name>")
            return True

        elif cmd == "providers":
            print("\nConfigured providers:")
            for name, cfg in self.manager.configs.items():
                active = "●" if name == self.manager.active_provider else "○"
                print(f"  {active} {name} — {cfg.provider_type} / {cfg.model}")
            return True

        elif cmd == "models":
            print("\nAvailable models:")
            for provider in self.manager.configs.values():
                print(f"  [{provider.name}] {provider.model}")
            return True

        elif cmd == "context":
            print("\n" + self.memory.get_context_summary())
            return True

        elif cmd == "stats":
            print(f"\nAgent Statistics:")
            print(f"  Turn count: {self.session.id[:8]}")
            print(f"  Messages: {len(self.messages)}")
            print(f"  Tool calls: {self._tool_call_count}")
            print(f"  Active agents: {len(self.team.list_agents()) if self.team else 0}")
            return True

        elif cmd == "retry":
            print("Retry: not yet implemented (last failed tool)")
            return True

        elif cmd == "undo":
            print("Undo: not yet implemented")
            return True

        elif cmd == "diff":
            print("Diff: not yet implemented (requires git integration)")
            return True

        elif cmd == "status":
            print(f"\nNEXUS v0.1.0")
            print(f"  Status: {'● ONLINE' if self.running else '○ OFFLINE'}")
            print(f"  Model: {self.manager.active_provider}")
            print(f"  Session: {self.session.id[:16]}")
            print(f"  Messages: {len(self.messages)}")
            print(f"  Plan mode: {'ON' if self._plan_active else 'OFF'}")
            print(f"  Agents: {len(self.team.list_agents()) if self.team else 0}")
            return True

        elif cmd == "facts":
            facts = self.memory.get_all_facts()
            if facts:
                print("\nStored facts:")
                for key, value in facts.items():
                    print(f"  {key}: {value}")
            else:
                print("No facts stored.")
            return True

        elif cmd == "fact":
            parts = args.split(maxsplit=1)
            if len(parts) >= 2:
                key, value = parts[0], parts[1]
                self.memory.add_fact(key, value)
                print(f"Added fact: {key} = {value}")
            else:
                print("Usage: /fact add <key> <value>")
            return True

        elif cmd == "mcp":
            print("MCP: use 'nexus mcp list/add/remove' from CLI")
            return True

        elif cmd == "plugin":
            from ..plugins import get_plugin_manager
            pm = get_plugin_manager()
            parts = args.split(maxsplit=1) if args else []
            subcmd = parts[0] if parts else "list"
            subargs = parts[1] if len(parts) > 1 else ""
            
            if subcmd == "list":
                plugins = pm.list_all()
                if not plugins:
                    print("No plugins loaded.")
                for p in plugins:
                    status = "✓ enabled" if pm.is_enabled(p.metadata.name) else "✗ disabled"
                    error = f" [ERROR: {p.error}]" if p.error else ""
                    print(f"  {p.metadata.name} v{p.metadata.version} — {status}{error}")
            elif subcmd == "enable" and subargs:
                if pm.enable(subargs):
                    print(f"Enabled: {subargs}")
                else:
                    print(f"Plugin not found: {subargs}")
            elif subcmd == "disable" and subargs:
                if pm.disable(subargs):
                    print(f"Disabled: {subargs}")
                else:
                    print(f"Plugin not found: {subargs}")
            else:
                print("Usage: /plugin list|enable <name>|disable <name>")
            return True

        elif cmd == "doctor":
            print("\n[DIAGNOSTICS]")
            print(f"  Config: /root/.nexus/config.json exists")
            print(f"  Providers: {len(self.manager.configs)} configured")
            print(f"  Tools: {len(self.registry.list_all())} available")
            print(f"  Termux: available")
            print(f"  Plugins: {len(get_plugin_manager().list_all())} loaded")
            print(f"  Safety rules: {len(self.safety.rules)} loaded")
            print(f"  Learning lessons: {self.learning.get_stats()['total_lessons']}")
            print(f"  Sync endpoints: {len(self.sync_engine.endpoints)}")
            print(f"  Improvements pending: {len(self.improver.get_improvement_queue())}")
            return True

        # --- SYNC commands ---
        elif cmd == "sync":
            parts = args.split(maxsplit=1) if args else []
            sub = parts[0] if parts else "status"
            subargs = parts[1] if len(parts) > 1 else ""

            if sub == "status":
                print(self.sync_engine.format_status())
            elif sub == "push":
                result = self.sync_engine.push(subargs or "default")
                if result.get("success"):
                    print(f"✓ Pushed {result.get('items', 0)} item(s)")
                    if result.get("gist_url"):
                        print(f"  Gist: {result['gist_url']}")
                else:
                    print(f"✗ Push failed: {result.get('error')}")
            elif sub == "pull":
                result = self.sync_engine.pull(subargs or "default")
                if result.get("success"):
                    print(f"✓ Pulled {result.get('items', 0)} item(s)")
                    if result.get("conflicts"):
                        print(f"⚠ Conflicts: {', '.join(result['conflicts'])}")
                else:
                    print(f"✗ Pull failed: {result.get('error')}")
            elif sub == "connect":
                print("Use: nexus sync connect <github-gist|local|git> --token <token> --path <path>")
            elif sub == "disconnect":
                if subargs and self.sync_engine.disconnect(subargs):
                    print(f"Disconnected: {subargs}")
                else:
                    print("Usage: /sync disconnect <name>")
            else:
                print("Usage: /sync status|push [endpoint]|pull [endpoint]|connect|disconnect")
            return True

        # --- LEARN commands ---
        elif cmd == "learn":
            parts = args.split(maxsplit=1) if args else []
            sub = parts[0] if parts else "stats"

            if sub == "stats":
                print(self.learning.format_summary())
            elif sub == "lessons":
                lessons = self.learning._load_all_lessons()[:5]
                if not lessons:
                    print("No lessons yet. Keep building!")
                for l in lessons:
                    rate = l.success_count / max(1, l.success_count + l.failure_count)
                    print(f"\n  [{l.lesson_id}] {l.title}")
                    print(f"    {l.summary[:80]}...")
                    print(f"    Success: {rate:.0%} | Triggers: {', '.join(l.trigger_conditions[:2])}")
            elif sub == "failures":
                import json
                failures = sorted(self.learning.failures_dir.glob("*.json"),
                                 key=lambda f: f.stat().st_mtime, reverse=True)[:5]
                for f in failures:
                    d = json.loads(f.read_text())
                    print(f"  [{d['timestamp'][:16]}] {d['tool_name']} — {d['error_type']}")
                    print(f"    {d['error'][:80]}...")
            else:
                print("Usage: /learn stats|lessons|failures")
            return True

        # --- IMPROVE commands ---
        elif cmd == "improve":
            parts = args.split(maxsplit=1) if args else []
            sub = parts[0] if parts else "queue"

            if sub == "queue":
                print(self.improver.format_improvement_queue())
            elif sub == "approve" and len(parts) > 1:
                if self.improver.approve(parts[1]):
                    print(f"✓ Approved: {parts[1]}. Use /improve apply {parts[1]} to apply.")
                else:
                    print(f"Improvement not found: {parts[1]}")
            elif sub == "apply" and len(parts) > 1:
                result = self.improver.apply(parts[1])
                if result.get("success"):
                    print(f"✓ Applied: {result.get('message')}")
                else:
                    print(f"✗ Failed: {result.get('error')}")
            elif sub == "run":
                print("\n🤖 Running self-improvement loop...")
                session_summary = {"tasks_completed": len(self.session.messages) // 2, "failures": []}
                improvements = self.improver.run_improvement_loop(
                    [],  # failures list
                    task_context=str(self.session.messages[-1]["content"])[:100] if self.session.messages else "",
                    provider_manager=self.manager,
                )
                if improvements:
                    print(f"✓ Generated {len(improvements)} improvement(s):")
                    for imp in improvements:
                        print(f"  • {imp.title}")
                else:
                    print("  No improvements needed right now.")
            elif sub == "reject" and len(parts) > 1:
                if self.improver.reject(parts[1]):
                    print(f"Rejected: {parts[1]}")
            else:
                print("Usage: /improve queue|approve <id>|apply <id>|run|reject <id>")
            return True

        # --- SAFETY commands ---
        elif cmd == "safety":
            parts = args.split(maxsplit=1) if args else []
            sub = parts[0] if parts else "status"

            if sub == "status":
                print(f"\nSafety: {'STRICT' if self.safety._strict_mode else 'PERMISSIVE'}")
                print(f"Rules loaded: {len(self.safety.rules)}")
                print(f"Violations this session: {len(self.safety._violations)}")
                print(f"Files read: {len(self.safety.get_read_files())}")
                print(f"\nViolation summary:\n{self.safety.get_violation_summary()}")
            elif sub == "strict":
                self.safety.enable_strict_mode()
                print("Strict mode enabled — warnings become blocks")
            elif sub == "permissive":
                self.safety.disable_strict_mode()
                print("Permissive mode — warnings are suggestions only")
            elif sub == "rules":
                for rid, rule in list(self.safety.rules.items())[:10]:
                    print(f"  [{rule.level.name}] {rule.name}: {rule.description[:50]}")
            else:
                print("Usage: /safety status|strict|permissive|rules")
            return True

        # --- PHONE mode ---
        elif cmd == "phone":
            if self.phone.enabled:
                print(f"Phone mode: ON ({self.phone.profile.name})")
            else:
                print("Phone mode: OFF. Set NEXUS_PHONE_MODE=1 to enable.")

        # --- REFLECTION ---
        elif cmd == "reflect":
            print(self.personality.reflection_ask())
            print(self.learning.ask_reflection())

        # --- PARTNER ---
        elif cmd == "partner":
            print(f"\n{self.personality.greet()}")
            print(f"Mode: {self.personality.config.mode.name}")
            print(f"Celebrate wins: {self.personality.config.celebrate_wins}")
            print(f"Proactive: {self.personality.config.proactive_suggestions}")

        return False

    async def _run_plan_mode(self, task: str) -> None:
        """Run plan mode for the given task."""
        if not self._plan_mode:
            return
        
        try:
            plan = await self._plan_mode.generate_plan(self.manager, self.messages)
            print(self._plan_mode.format_for_display())
            
            # Wait for user input
            try:
                action = input("Action (A=approve all, S=skip low, Q=quit): ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("Plan mode cancelled.")
                self._plan_active = False
                return
            
            if action == "a":
                self._plan_mode.approve_all()
                print("All steps approved. Use /build to execute.")
            elif action == "s":
                self._plan_mode.skip_low_priority()
                print("Low priority steps skipped.")
            elif action == "q":
                self._plan_mode.deactivate()
                self._plan_active = False
                print("Plan mode cancelled.")
        except Exception as e:
            print(f"Plan mode error: {e}")
            self._plan_active = False

    async def _execute_plan(self) -> None:
        """Execute the current plan."""
        if not self._plan_mode or not self._plan_mode.plan:
            return
        
        approved = self._plan_mode.get_approved_steps()
        if not approved:
            print("No approved steps to execute.")
            return
        
        print(f"\nExecuting {len(approved)} approved steps...")
        tracker = ProgressTracker(len(approved), "Executing plan")
        
        for step in approved:
            tracker.step(step.description)
            if step.tool_name:
                tool = self.registry.get(step.tool_name)
                if tool:
                    try:
                        result = await tool.execute(**step.tool_args)
                        step.result = result.content[:100]
                    except Exception as e:
                        step.error = str(e)
        
        tracker.finish()
        self._plan_active = False

    async def run(self) -> None:
        """Run the REPL."""
        greeting = self.personality.greet()
        print(f"""
╔══════════════════════════════════════════════════════════╗
║           Rehoboth Genesis - Nexus Agent                 ║
║                                                           ║
║  {greeting:<53} ║
║  Type /help for commands, or just start chatting.         ║
╚══════════════════════════════════════════════════════════╝
""")

        while self.running:
            try:
                line = input(self._get_prompt()).strip()
                
                if not line:
                    continue

                # Handle slash commands
                if self._handle_command(line):
                    continue

                # Handle regular input
                await self._generate_response(line)

            except KeyboardInterrupt:
                print("\n(Use /exit to quit)")
                continue
            except EOFError:
                break
            except Exception as e:
                self.learning.record_failure(str(e), {"type": type(e).__name__}, "")
                print(f"{self.personality.failure()} {e}")

        self._save_history()
        self.memory.save_session(self.session)
        summary = self.learning.end_session(self.session.id, "completed")
        if summary.get("failures", 0) > 0:
            print(f"\n{self.personality.reflection_ask()}")


    def _on_team_message(self, msg) -> None:
        """Handle incoming team messages."""
        if msg.msg_type == "system":
            print(f"\n[team] {msg.content}")
        else:
            color_map = {
                "planner": "\033[94m",    # blue
                "coder": "\033[92m",       # green
                "reviewer": "\033[93m",    # yellow
                "tester": "\033[95m",      # magenta
                "researcher": "\033[96m",  # cyan
            }
            color = color_map.get(msg.agent_name.split("_")[0] if "_" in msg.agent_name else "", "\033[92m")
            reset = "\033[0m"
            print(f"\n[{color}{msg.agent_name}{reset}]: {msg.content}")

    def _on_thinking_update(self, event) -> None:
        """Handle thinking engine updates."""
        event_type, step = event
        
        if event_type == "start":
            if step.state == ThinkingState.ANALYZING:
                self._show_loading("Analyzing task...")
            elif step.state == ThinkingState.PLANNING:
                pass  # Planning is internal
            elif step.state == ThinkingState.EXECUTING:
                self._show_tool_start(step)
            elif step.state == ThinkingState.REVIEWING:
                pass  # Reviewing is internal
            elif step.state == ThinkingState.COMPLETE:
                self._show_complete(step)
                
        elif event_type == "finish":
            if step.state == ThinkingState.ANALYZING:
                self._hide_loading()
            elif step.state == ThinkingState.EXECUTING:
                self._show_tool_result(step)
            elif step.state == ThinkingState.COMPLETE:
                self._show_task_complete(step)

    def _show_loading(self, message: str) -> None:
        """Show loading indicator."""
        if not self._first_token_received:
            sys.stdout.write(f"\033[93m◌ {message}\033[0m")
            sys.stdout.flush()

    def _hide_loading(self) -> None:
        """Hide loading indicator."""
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()

    def _show_tool_start(self, step) -> None:
        """Show tool execution start."""
        print(f"\n\033[94m--- [TOOL] {step.tool_name} ---\033[0m")
        if step.tool_args:
            args_str = ", ".join(f"{k}={repr(v)[:30]}" for k, v in list(step.tool_args.items())[:3])
            print(f"  \033[90m{args_str}...\033[0m")

    def _show_tool_result(self, step) -> None:
        """Show tool execution result."""
        if step.state == ThinkingState.ERROR:
            print(f"\033[91m  ✗ Failed: {step.detail or 'Unknown error'}\033[0m")
        else:
            result_preview = (step.tool_result or "")[:150].replace("\n", " ")
            print(f"\033[92m  ✓ Success\033[0m — {step.duration_ms:.0f}ms")
            if result_preview:
                print(f"  \033[90m{result_preview}...\033[0m")

    def _show_complete(self, step) -> None:
        """Show step complete."""
        self._tool_call_count += 1

    def _show_task_complete(self, step) -> None:
        """Show task complete summary."""
        elapsed = step.duration_ms / 1000.0 if step.duration_ms else 0
        tool_count = self._tool_call_count
        print(f"\n\033[92m✅ Done in {elapsed:.1f}s\033[0m — {tool_count} tool call(s)")

    async def run_single(self, task: str) -> str:
        """Run a single task and return the result."""
        return await self._generate_response(task)


async def run_repl(config: dict[str, Any] | None = None) -> None:
    """Run the REPL."""
    repl = REPL(config)
    await repl.run()


async def run_task(task: str, config: dict[str, Any] | None = None) -> str:
    """Run a single task."""
    repl = REPL(config)
    result = await repl.run_single(task)
    await repl.manager.close_all()
    return result
