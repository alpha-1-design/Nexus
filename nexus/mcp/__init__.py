"""MCP (Model Context Protocol) client for Nexus.

MCP is a protocol for connecting AI models to external tools and data sources.
Nexus MCP client supports:
  - stdio transport (local servers, JSON-RPC over stdin/stdout)
  - SSE transport (remote servers)
  - Lazy tool loading (only load when needed)
  - Tool registry integration: installed servers are exposed to the agent as
    adapter tools via `register_mcp_tools` (called by `tools.get_registry`).

Inspired by: Claude Code's lazy-loading MCP tools, Gemini CLI's ToolRegistry cloning.
"""

import asyncio
import http.client
import json
import os
import re
import subprocess
import threading
import urllib.parse
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..tools.base import BaseTool, ToolDefinition, ToolRegistry, ToolResult

# Wire protocol (MCP 2024-11-05).
_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "nexus", "version": "0.1.0"}

# Seconds to wait for a JSON-RPC response before giving up on a server.
# Override with NEXUS_MCP_TIMEOUT (integer seconds).
_DEFAULT_REQUEST_TIMEOUT = float(os.environ.get("NEXUS_MCP_TIMEOUT", "30") or 30)

# Where `nexus mcp add/install` (CLI + dashboard) persist installed servers.
# Tests can point NEXUS_MCP_CONFIG at a temporary file instead.
_CONFIG_PATH_ENV = "NEXUS_MCP_CONFIG"
_DEFAULT_CONFIG_PATH = Path.home() / ".nexus" / "mcp-servers.json"

# Lines of server stderr kept around for error messages, and max number of
# non-matching lines tolerated while waiting for a single response.
_STDERR_TAIL_LINES = 20
_MAX_SKIPPED_LINES = 1000


class MCPServerError(RuntimeError):
    """Raised when an MCP server fails to start, answer, or speak the protocol."""


def default_config_path() -> Path:
    """Path of the installed-servers JSON file written by `nexus mcp add/install`."""
    env = os.environ.get(_CONFIG_PATH_ENV)
    return Path(env) if env else _DEFAULT_CONFIG_PATH


def _sanitize_tool_name(name: str) -> str:
    """Make an MCP server name safe for use in a Nexus tool id."""
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", name).strip("_") or "server"


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    command: str = ""  # stdio transport: executable (empty for SSE)
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    transport: str = "stdio"
    auto_load: bool = True


@dataclass
class MCPTool:
    """A tool exposed by an MCP server."""

    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


def load_server_configs(
    config_path: Path | None = None,
) -> tuple[dict[str, MCPServerConfig], dict[str, str]]:
    """Load installed MCP server configs from the shared JSON file.

    Returns (configs, errors). Entries that fail validation are skipped and
    reported in `errors` (keyed by server name), so one broken entry can't
    hide the rest.
    """
    path = config_path or default_config_path()
    configs: dict[str, MCPServerConfig] = {}
    errors: dict[str, str] = {}
    if not path.is_file():
        return configs, errors
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        return configs, {"(file)": f"Cannot read {path}: {e}"}
    if not isinstance(data, dict):
        return configs, {"(file)": f"{path} must contain a JSON object of servers"}

    valid_fields = set(MCPServerConfig.__dataclass_fields__)
    for name, cfg in data.items():
        name = str(name)
        if not isinstance(cfg, dict):
            errors[name] = "entry must be a JSON object"
            continue
        entry = {key: value for key, value in cfg.items() if key in valid_fields}
        if isinstance(entry.get("args"), str):
            entry["args"] = entry["args"].split()
        elif isinstance(entry.get("args"), (list, tuple)):
            entry["args"] = [str(arg) for arg in entry["args"]]
        else:
            entry.pop("args", None)
        try:
            configs[name] = MCPServerConfig(name=name, **entry)
        except TypeError as e:
            errors[name] = f"Invalid server config: {e}"
    return configs, errors


class MCPClient:
    """
    MCP client that connects to MCP servers and exposes their tools.
    Implements the MCP protocol over stdio or SSE transport.
    """

    def __init__(self, request_timeout: float | None = None):
        self._servers: dict[str, MCPServerConfig] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._tools: dict[str, MCPTool] = {}
        self._initialized: set[str] = set()
        self._stderr_tails: dict[str, deque[str]] = {}
        self._stderr_threads: dict[str, threading.Thread] = {}
        self._sse_endpoints: dict[str, str] = {}
        self._next_request_id = 1
        self.request_timeout = (
            request_timeout if request_timeout is not None else _DEFAULT_REQUEST_TIMEOUT
        )

    def add_server(self, config: MCPServerConfig) -> None:
        """Register an MCP server configuration."""
        self._servers[config.name] = config

    def remove_server(self, name: str) -> None:
        """Remove and stop an MCP server."""
        self._stop_process(name)
        self._servers.pop(name, None)
        self._stderr_tails.pop(name, None)
        self._sse_endpoints.pop(name, None)
        self._initialized.discard(name)
        self._tools = {k: v for k, v in self._tools.items() if v.server_name != name}

    def get_server(self, name: str) -> MCPServerConfig | None:
        """Get a configured server by name."""
        return self._servers.get(name)

    async def initialize_server(self, name: str) -> None:
        """Initialize an MCP server via JSON-RPC handshake."""
        config = self._servers.get(name)
        if not config:
            raise ValueError(f"MCP server '{name}' not configured")

        if config.transport == "stdio":
            await self._init_stdio(config)
        elif config.transport == "sse":
            await self._init_sse(config)
        else:
            raise MCPServerError(f"Server '{name}' has unknown transport '{config.transport}'")

        self._initialized.add(name)

    async def _init_stdio(self, config: MCPServerConfig) -> None:
        """Initialize a stdio-based MCP server: spawn, handshake, list tools."""
        if not config.command:
            raise MCPServerError(f"Server '{config.name}' has no command configured")
        env = {**os.environ, **config.env}
        try:
            proc = subprocess.Popen(
                [config.command] + config.args,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError:
            raise MCPServerError(
                f"Server '{config.name}': command not found: {config.command}"
            ) from None
        except OSError as e:
            raise MCPServerError(f"Server '{config.name}' failed to start: {e}") from e

        self._processes[config.name] = proc
        self._stderr_tails[config.name] = deque(maxlen=_STDERR_TAIL_LINES)
        self._start_stderr_drain(config.name, proc)

        try:
            await self._send_request(config.name, "initialize", {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": _CLIENT_INFO,
            })
            await self._send_notification(
                config.name, {"jsonrpc": "2.0", "method": "notifications/initialized"}
            )
            tools_response = await self._send_request(config.name, "tools/list", {})
        except Exception:
            # Never leave a half-started server process behind.
            self._stop_process(config.name)
            raise

        self._register_server_tools(config.name, tools_response)

    # ── SSE transport plumbing (stdlib http.client) ──────────────────────

    def _sse_connection(self, url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
        """Open an HTTP(S) connection to `url`; returns (connection, request path)."""
        parts = urllib.parse.urlsplit(url)
        if not parts.scheme or not parts.netloc:
            raise MCPServerError(f"Invalid SSE server URL: {url!r}")
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parts.hostname, parts.port, timeout=timeout)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        return conn, path

    def _sse_discover_endpoint(self, config: MCPServerConfig) -> str:
        """GET {url}/sse and read the POST endpoint from the event stream.

        MCP SSE servers announce where to POST JSON-RPC messages with an
        ``event: endpoint`` / ``data: <uri>`` pair. Parsed line by line with
        stdlib only; the stream is closed as soon as the endpoint is known
        (a minimal client needs no further stream events).
        """
        conn = None
        try:
            parts = urllib.parse.urlsplit(config.url or "")
            sse_path = (parts.path or "").rstrip("/")
            if not sse_path.endswith("/sse"):
                sse_path += "/sse"
            conn, _ = self._sse_connection(config.url or "", self.request_timeout)
            conn.request("GET", sse_path, headers={"Accept": "text/event-stream"})
            resp = conn.getresponse()
            if resp.status != 200:
                raise MCPServerError(
                    f"Server '{config.name}': SSE endpoint returned HTTP {resp.status}"
                )
            event = ""
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode(errors="replace").strip()
                if line.startswith("event:"):
                    event = line[len("event:"):].strip()
                elif line.startswith("data:") and event == "endpoint":
                    target = line[len("data:"):].strip()
                    if target:
                        endpoint = urllib.parse.urljoin(config.url or "", target)
                        self._sse_endpoints[config.name] = endpoint
                        return endpoint
            raise MCPServerError(
                f"Server '{config.name}': SSE stream ended without an endpoint event"
            )
        except MCPServerError:
            raise
        except (OSError, http.client.HTTPException) as e:
            raise MCPServerError(f"Server '{config.name}' SSE endpoint failed: {e}") from e
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def _sse_post(self, config: MCPServerConfig, payload: dict) -> dict | None:
        """POST one JSON-RPC payload to the server's message endpoint.

        Returns the parsed response object, or None when the server answers
        without a body (e.g. 202 Accepted for notifications). Re-discovers the
        endpoint once if a cached one stopped accepting connections.
        """
        endpoint = self._sse_endpoints.get(config.name)
        for attempt in (1, 2):
            if not endpoint:
                endpoint = self._sse_discover_endpoint(config)
            conn = None
            try:
                conn, path = self._sse_connection(endpoint, self.request_timeout)
                body = json.dumps(payload).encode()
                conn.request(
                    "POST",
                    path,
                    body=body,
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
                resp = conn.getresponse()
                data = resp.read()
                if resp.status not in (200, 202):
                    snippet = data.decode(errors="replace")[:200]
                    raise MCPServerError(
                        f"Server '{config.name}': POST returned HTTP {resp.status}: {snippet}"
                    )
                if not data.strip():
                    return None
                try:
                    message = json.loads(data)
                except json.JSONDecodeError:
                    raise MCPServerError(
                        f"Server '{config.name}': non-JSON response to '{payload.get('method')}'"
                    ) from None
                if not isinstance(message, dict):
                    raise MCPServerError(
                        f"Server '{config.name}': unexpected response to '{payload.get('method')}'"
                    )
                return message
            except MCPServerError:
                raise
            except (OSError, http.client.HTTPException) as e:
                if attempt == 1 and self._sse_endpoints.pop(config.name, None):
                    endpoint = None  # stale endpoint: discover again, retry once
                    continue
                raise MCPServerError(
                    f"Server '{config.name}' request to '{payload.get('method')}' failed: {e}"
                ) from e
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
        return None  # pragma: no cover — unreachable

    async def _sse_request(
        self, config: MCPServerConfig, method: str, params: dict | None = None
    ) -> dict:
        """POST a JSON-RPC request over the SSE transport, id-matched response."""
        request_id = self._next_request_id
        self._next_request_id += 1
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        try:
            message = await asyncio.get_event_loop().run_in_executor(
                None, self._sse_post, config, payload
            )
        except MCPServerError:
            raise
        except Exception as e:
            raise MCPServerError(f"Server '{config.name}' request failed: {e}") from e
        if message is None:
            raise MCPServerError(f"Server '{config.name}' sent no response for '{method}'")
        if message.get("id") != request_id:
            raise MCPServerError(
                f"Server '{config.name}' returned a mismatched id for '{method}'"
            )
        if "error" in message:
            error = message["error"]
            detail = (
                error.get("message", json.dumps(error)) if isinstance(error, dict) else str(error)
            )
            raise MCPServerError(
                f"Server '{config.name}' returned an error for '{method}': {detail}"
            )
        return message

    async def _sse_notify(self, config: MCPServerConfig) -> None:
        """Send the initialized notification; a failure surfaces on the next request."""
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._sse_post, config, payload)
        except MCPServerError:
            pass

    async def _init_sse(self, config: MCPServerConfig) -> None:
        """Initialize an SSE-based MCP server.

        Handshake: GET {url}/sse to learn the POST endpoint, then JSON-RPC
        initialize -> notifications/initialized -> tools/list over POSTs.
        """
        try:
            await self._sse_request(config, "initialize", {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": _CLIENT_INFO,
            })
            await self._sse_notify(config)
            tools_response = await self._sse_request(config, "tools/list", {})
        except Exception:
            self._sse_endpoints.pop(config.name, None)
            raise
        self._register_server_tools(config.name, tools_response)

    # ── stdio JSON-RPC plumbing ───────────────────────────────────────────

    async def _send_request(self, server_name: str, method: str, params: dict | None = None) -> dict:
        """Send a JSON-RPC request to a stdio server and return its response.

        Responses are matched by id; server-initiated notifications and junk
        lines are skipped while waiting for the answer.
        """
        proc = self._processes.get(server_name)
        if not proc or proc.stdin is None or proc.stdout is None:
            raise MCPServerError(f"Server '{server_name}' not running")

        request_id = self._next_request_id
        self._next_request_id += 1
        await self._write_line(
            server_name,
            proc,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        )

        skipped = 0
        while True:
            line = await self._read_line(server_name, proc)
            if not line:
                raise MCPServerError(self._disconnect_message(server_name, proc))
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                if skipped > _MAX_SKIPPED_LINES:
                    raise MCPServerError(
                        f"Server '{server_name}' sent {skipped} non-protocol lines "
                        f"while waiting for a response to '{method}'"
                    ) from None
                continue
            if not isinstance(message, dict):
                continue
            if "id" not in message:
                continue  # server-initiated notification, not the answer
            if message.get("id") != request_id:
                continue  # answer to something else; keep waiting
            if "error" in message:
                error = message["error"]
                detail = (
                    error.get("message", json.dumps(error))
                    if isinstance(error, dict)
                    else str(error)
                )
                raise MCPServerError(
                    f"Server '{server_name}' returned an error for '{method}': {detail}"
                )
            return message

    async def _send_notification(self, server_name: str, notification: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        proc = self._processes.get(server_name)
        if not proc or proc.stdin is None:
            return
        try:
            await self._write_line(server_name, proc, notification)
        except MCPServerError:
            pass  # a dead server surfaces on the next real request

    async def _write_line(self, server_name: str, proc: subprocess.Popen, payload: dict) -> None:
        """Serialize and write one JSON-RPC message without blocking the loop."""
        loop = asyncio.get_event_loop()
        data = (json.dumps(payload) + "\n").encode()
        try:
            await loop.run_in_executor(None, proc.stdin.write, data)
            await loop.run_in_executor(None, proc.stdin.flush)
        except (BrokenPipeError, OSError, ValueError):
            raise MCPServerError(self._disconnect_message(server_name, proc)) from None

    async def _read_line(self, server_name: str, proc: subprocess.Popen) -> str:
        """Read one stdout line, bounded by the request timeout."""
        loop = asyncio.get_event_loop()
        try:
            line = await asyncio.wait_for(
                loop.run_in_executor(None, proc.stdout.readline), timeout=self.request_timeout
            )
        except asyncio.TimeoutError:
            raise MCPServerError(
                f"Server '{server_name}' timed out after {self.request_timeout:g}s"
            ) from None
        return line.decode(errors="replace") if isinstance(line, bytes) else line

    def _disconnect_message(self, server_name: str, proc: subprocess.Popen) -> str:
        """Human-readable failure text, including the server's stderr tail."""
        if proc.poll() is not None:
            base = f"Server '{server_name}' exited unexpectedly (code {proc.returncode})"
        else:
            base = f"Server '{server_name}' disconnected"
        tail = " ".join(self._stderr_tails.get(server_name, ())).strip()
        return f"{base}; stderr: {tail}" if tail else base

    def _start_stderr_drain(self, server_name: str, proc: subprocess.Popen) -> None:
        """Drain the server's stderr in a background thread.

        A server that writes to stderr would otherwise fill the OS pipe buffer
        and block forever; we keep the tail for error messages instead.
        """
        tail = self._stderr_tails[server_name]
        stream = proc.stderr

        def _drain() -> None:
            if stream is None:
                return
            try:
                for raw in iter(stream.readline, b""):
                    text = raw.decode(errors="replace").strip()
                    if text:
                        tail.append(text)
            except (OSError, ValueError):
                pass

        thread = threading.Thread(
            target=_drain, name=f"nexus-mcp-stderr-{server_name}", daemon=True
        )
        thread.start()
        self._stderr_threads[server_name] = thread

    def _stop_process(self, name: str) -> None:
        """Terminate a server process and clean up its bookkeeping.

        The captured stderr tail is kept on purpose: it is the only trace of
        why the server died, and `_init_stdio` replaces it on the next start.
        """
        proc = self._processes.pop(name, None)
        self._stderr_threads.pop(name, None)
        if proc is None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass

    # ── tool calls ────────────────────────────────────────────────────────

    async def call_tool(self, full_name: str, arguments: dict[str, Any]) -> ToolResult:
        """
        Call an MCP tool by its full name (server/tool).
        Returns ToolResult for integration with the tool registry.
        """
        if "/" not in full_name:
            return ToolResult(success=False, content="", error=f"Invalid MCP tool name: {full_name}")

        server_name, tool_name = full_name.split("/", 1)
        if server_name not in self._initialized:
            try:
                await self.initialize_server(server_name)
            except Exception as e:
                return ToolResult(
                    success=False, content="", error=f"Failed to initialize {server_name}: {e}"
                )

        available = sorted(
            t.name for t in self._tools.values() if t.server_name == server_name
        )
        if available and tool_name not in available:
            return ToolResult(
                success=False,
                content="",
                error=(
                    f"Tool '{tool_name}' not found on MCP server '{server_name}'. "
                    f"Available tools: {', '.join(available)}"
                ),
            )

        config = self._servers[server_name]
        if config.transport == "stdio":
            return await self._call_stdio_tool(server_name, tool_name, arguments)
        elif config.transport == "sse":
            return await self._call_sse_tool(config, tool_name, arguments)
        return ToolResult(success=False, content="", error=f"Unknown transport: {config.transport}")

    async def _call_stdio_tool(self, server_name: str, tool_name: str, args: dict) -> ToolResult:
        """Call a tool on a stdio MCP server."""
        try:
            response = await self._send_request(
                server_name, "tools/call", {"name": tool_name, "arguments": args}
            )
        except MCPServerError as e:
            return ToolResult(success=False, content="", error=str(e))
        except Exception as e:
            return ToolResult(success=False, content="", error=f"MCP call failed: {e}")

        return self._tool_result_from_response(server_name, tool_name, response)

    def _register_server_tools(self, server_name: str, tools_response: dict) -> None:
        """Store the tools advertised by a tools/list response."""
        for tool_data in tools_response.get("result", {}).get("tools", []):
            if not isinstance(tool_data, dict) or "name" not in tool_data:
                continue  # a malformed tool entry must not sink the whole server
            self._tools[f"{server_name}/{tool_data['name']}"] = MCPTool(
                name=tool_data["name"],
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}) or {},
                server_name=server_name,
            )

    def _tool_result_from_response(
        self, server_name: str, tool_name: str, response: dict
    ) -> ToolResult:
        """Map a JSON-RPC tools/call result onto a ToolResult."""
        result = response.get("result", {})
        if not isinstance(result, dict):
            result = {}
        content = result.get("content", [])
        if isinstance(content, list):
            text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        else:
            text = str(content)
        if result.get("isError"):
            return ToolResult(
                success=False,
                content=text,
                error=text or f"MCP tool '{tool_name}' failed on server '{server_name}'",
            )
        return ToolResult(success=True, content=text)

    async def _call_sse_tool(self, config: MCPServerConfig, tool_name: str, args: dict) -> ToolResult:
        """Call a tool on an SSE MCP server (JSON-RPC over POST)."""
        try:
            response = await self._sse_request(
                config, "tools/call", {"name": tool_name, "arguments": args}
            )
        except MCPServerError as e:
            return ToolResult(success=False, content="", error=str(e))
        except Exception as e:
            return ToolResult(success=False, content="", error=f"MCP call failed: {e}")
        return self._tool_result_from_response(config.name, tool_name, response)

    def list_tools(self) -> list[MCPTool]:
        """List all available MCP tools."""
        return list(self._tools.values())

    def get_tool(self, full_name: str) -> MCPTool | None:
        """Get an MCP tool by full name."""
        return self._tools.get(full_name)

    async def close(self) -> None:
        """Stop all MCP servers."""
        for name in list(self._processes):
            self._stop_process(name)
        self._stderr_tails.clear()
        self._sse_endpoints.clear()
        self._initialized.clear()


# ── tool-registry integration ─────────────────────────────────────────────


def mcp_tool_from_server(mcp: MCPClient, server_name: str) -> type[BaseTool]:
    """Create a BaseTool subclass that wraps all tools from an MCP server."""

    class MCPAdapterTool(BaseTool):
        _mcp = mcp
        _server = server_name

        @property
        def definition(self) -> ToolDefinition:
            tools = sorted(
                (t for t in self._mcp.list_tools() if t.server_name == self._server),
                key=lambda t: t.name,
            )
            if tools:
                parts = []
                for tool in tools:
                    part = tool.name
                    if tool.description:
                        part += f" — {tool.description[:100]}"
                    required = (
                        tool.input_schema.get("required") if isinstance(tool.input_schema, dict) else None
                    )
                    if required:
                        part += f" (required args: {', '.join(map(str, required))})"
                    parts.append(part)
                desc = (
                    f"MCP server '{self._server}' with {len(tools)} tool(s): "
                    + "; ".join(parts)
                    + ". Call with tool=<name> and args=<object>."
                )
            else:
                config = self._mcp.get_server(self._server)
                target = ""
                if config and config.transport == "stdio":
                    cmd = " ".join([config.command] + list(config.args)).strip()
                    target = f" (command: {cmd})"
                elif config and config.url:
                    target = f" (url: {config.url})"
                desc = (
                    f"MCP server '{self._server}'{target}. Tools are discovered on first use: "
                    "the first call starts the server, and an unknown tool name returns "
                    "the list of available tools."
                )
            return ToolDefinition(
                name=f"mcp_{_sanitize_tool_name(self._server)}",
                description=desc,
                input_schema={
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "Name of the MCP tool to call"},
                        "args": {"type": "object", "description": "Arguments dict for the MCP tool"},
                    },
                    "required": ["tool"],
                },
                requires_permission=True,
                category="mcp",
            )

        async def execute(self, **kwargs) -> ToolResult:
            tool_name = str(kwargs.get("tool", "") or "")
            args = kwargs.get("args")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    return ToolResult(
                        success=False,
                        content="",
                        error=f"'args' must be a JSON object, got unparseable string: {args[:200]}",
                    )
            if not isinstance(args, dict):
                args = {}
            if not tool_name:
                return ToolResult(success=False, content="", error="No tool specified")
            return await self._mcp.call_tool(f"{self._server}/{tool_name}", args)

    return MCPAdapterTool


_client: MCPClient | None = None


def get_client() -> MCPClient:
    """Get the shared MCPClient used by registered adapters."""
    global _client
    if _client is None:
        _client = MCPClient()
    return _client


def register_mcp_tools(registry: ToolRegistry, config_path: Path | None = None) -> list[str]:
    """Register an adapter tool for every installed MCP server.

    Registration is lazy and non-fatal: it only reads the installed-servers
    JSON (no processes are started), and each adapter connects to its server
    on first use. A missing, corrupt or half-broken config never raises —
    the agent keeps working with its core tools. Returns registered tool names.
    """
    registered: list[str] = []
    try:
        configs, _errors = load_server_configs(config_path)
    except Exception:
        return registered

    client = get_client()
    existing_names = {definition.name for definition in registry.list_all()}
    for name, config in configs.items():
        tool_name = f"mcp_{_sanitize_tool_name(name)}"
        if tool_name in existing_names:
            continue
        try:
            client.add_server(config)
            adapter = mcp_tool_from_server(client, name)()
            registry.register(adapter)
            existing_names.add(tool_name)
            registered.append(tool_name)
        except Exception:
            continue  # one broken server entry must not block the others
    return registered


def collect_server_status(
    config_path: Path | None = None, connect: bool = False
) -> list[dict[str, Any]]:
    """Status of every installed MCP server, for `nexus mcp list` / dashboards.

    Without `connect` this is a fast config-only snapshot. With `connect=True`
    each server is actually started and asked for its tool list, so `connected`
    and `tool_count` reflect reality (bounded by the request timeout).
    """
    configs, errors = load_server_configs(config_path)
    status: list[dict[str, Any]] = []
    for name, config in sorted(configs.items()):
        status.append({
            "name": name,
            "transport": config.transport,
            "target": config.command if config.transport == "stdio" else (config.url or ""),
            "connected": False,
            "tool_count": 0,
            "tools": [],
            "error": errors.get(name, ""),
        })
    known = {entry["name"] for entry in status}
    for name, message in errors.items():
        if name not in known:
            status.append({
                "name": name,
                "transport": "unknown",
                "target": "",
                "connected": False,
                "tool_count": 0,
                "tools": [],
                "error": message,
            })
    if not connect:
        return status

    async def _probe() -> None:
        client = MCPClient()
        try:
            for name, config in configs.items():
                client.add_server(config)
                entry = next(e for e in status if e["name"] == name)
                try:
                    await client.initialize_server(name)
                    tools = [t for t in client.list_tools() if t.server_name == name]
                    entry["connected"] = True
                    entry["tool_count"] = len(tools)
                    entry["tools"] = [t.name for t in tools]
                except Exception as e:
                    entry["error"] = str(e)
        finally:
            await client.close()

    asyncio.run(_probe())
    return status
