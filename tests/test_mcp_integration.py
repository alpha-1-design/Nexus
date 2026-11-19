"""End-to-end tests for the MCP subsystem.

Uses tiny fake MCP servers (real subprocesses speaking JSON-RPC over stdio)
so the handshake initialize -> tools/list -> tools/call round trip is tested
without any network access.
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from nexus import mcp as mcp_module
from nexus.mcp import (
    MCPClient,
    MCPServerConfig,
    MCPServerError,
    collect_server_status,
    default_config_path,
    load_server_configs,
    mcp_tool_from_server,
    register_mcp_tools,
)
from nexus.tools.base import ToolRegistry

# ── fake MCP server scripts (stdlib only) ──────────────────────────────────

FAKE_OK_SERVER = r'''
"""Fake MCP stdio server: initialize -> tools/list -> tools/call."""
import json
import sys

TOOLS = [
    {
        "name": "echo",
        "description": "Echo back the input message",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "add",
        "description": "Add two numbers",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        },
    },
    {
        "name": "fail",
        "description": "Always reports a tool-level failure",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def main():
    sys.stderr.write("fake server starting\n")
    sys.stderr.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        request_id = msg.get("id")
        if request_id is None:
            continue  # notification: nothing to answer
        method = msg.get("method")
        if method == "initialize":
            out = {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-ok", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            out = {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                # A well-behaved server may emit notifications before the
                # response; the client must skip them and match by id.
                sys.stdout.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/progress",
                            "params": {"progress": 1},
                        }
                    )
                    + "\n"
                )
                sys.stdout.flush()
                out = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {"type": "text", "text": "echo: " + str(args.get("message", ""))}
                        ]
                    },
                }
            elif name == "add":
                total = float(args.get("a", 0)) + float(args.get("b", 0))
                out = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": str(total)}]},
                }
            elif name == "fail":
                out = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "boom"}],
                    },
                }
            else:
                out = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {name}"},
                }
        else:
            out = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
'''

# Answers `initialize`, then dies before tools/list (and chatters on stderr).
FAKE_DIE_AFTER_INIT_SERVER = r'''
import json
import sys

sys.stderr.write("starting up\n")
sys.stderr.flush()
line = sys.stdin.readline()
msg = json.loads(line)
out = {
    "jsonrpc": "2.0",
    "id": msg["id"],
    "result": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "serverInfo": {"name": "fake-die", "version": "1.0.0"},
    },
}
sys.stdout.write(json.dumps(out) + "\n")
sys.stdout.flush()
sys.exit(0)
'''

# Emits a non-JSON line, then exits: the client must surface a clean failure.
FAKE_GARBAGE_SERVER = r'''
import sys

sys.stdout.write("I am not a JSON-RPC server\n")
sys.stdout.flush()
sys.exit(0)
'''

# Reads one request, never answers (used with a short client timeout).
FAKE_HUNG_SERVER = r'''
import sys
import time

sys.stdin.readline()
time.sleep(60)
'''

# Writes to stderr, then goes quiet (exercises the stderr drain thread).
FAKE_NOISY_SERVER = r'''
import sys
import time

sys.stderr.write("noise for the stderr tail\n")
sys.stderr.flush()
for line in sys.stdin:
    time.sleep(1)
'''


def _write_script(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def _ok_config(tmp_path: Path, name: str = "fake") -> MCPServerConfig:
    script = _write_script(tmp_path, "fake_ok_server.py", FAKE_OK_SERVER)
    return MCPServerConfig(name=name, command=sys.executable, args=[str(script)])


@pytest.fixture()
def isolated_mcp_config(monkeypatch, tmp_path):
    """Point NEXUS_MCP_CONFIG at a temp file and reset shared singletons."""
    config_path = tmp_path / "servers.json"
    monkeypatch.setenv("NEXUS_MCP_CONFIG", str(config_path))
    monkeypatch.setattr(mcp_module, "_client", None)
    return config_path


# ── handshake + round trip ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handshake_discovers_tools(tmp_path):
    client = MCPClient()
    try:
        client.add_server(_ok_config(tmp_path))
        await client.initialize_server("fake")
        assert "fake" in client._initialized

        tools = {t.name: t for t in client.list_tools()}
        assert set(tools) == {"echo", "add", "fail"}
        assert tools["echo"].server_name == "fake"
        assert tools["echo"].description == "Echo back the input message"
        assert tools["echo"].input_schema.get("required") == ["message"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_tool_call_round_trip(tmp_path):
    client = MCPClient()
    try:
        client.add_server(_ok_config(tmp_path))
        result = await client.call_tool("fake/echo", {"message": "hello"})
        assert result.success is True
        assert result.content == "echo: hello"

        result = await client.call_tool("fake/add", {"a": 2, "b": 3})
        assert result.success is True
        assert float(result.content) == 5.0
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_iserror_result_is_a_failure(tmp_path):
    client = MCPClient()
    try:
        client.add_server(_ok_config(tmp_path))
        result = await client.call_tool("fake/fail", {})
        assert result.success is False
        assert result.error == "boom"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unknown_tool_lists_available(tmp_path):
    client = MCPClient()
    try:
        client.add_server(_ok_config(tmp_path))
        result = await client.call_tool("fake/nope", {})
        assert result.success is False
        assert "nope" in result.error
        assert "echo" in result.error and "add" in result.error
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_invalid_tool_name_rejected():
    client = MCPClient()
    result = await client.call_tool("no-slash-here", {})
    assert result.success is False
    assert "Invalid MCP tool name" in result.error


# ── graceful failure on broken servers ─────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_command_fails_cleanly():
    client = MCPClient()
    client.add_server(
        MCPServerConfig(name="ghost", command="nexus-definitely-missing-cmd-xyz")
    )
    result = await client.call_tool("ghost/echo", {})
    assert result.success is False
    assert "command not found" in result.error
    assert "ghost" in result.error
    assert "ghost" not in client._initialized


@pytest.mark.asyncio
async def test_garbage_output_fails_cleanly(tmp_path):
    script = _write_script(tmp_path, "fake_garbage.py", FAKE_GARBAGE_SERVER)
    client = MCPClient()
    try:
        client.add_server(MCPServerConfig(name="junk", command=sys.executable, args=[str(script)]))
        result = await client.call_tool("junk/echo", {})
        assert result.success is False
        assert "junk" in result.error
        # Either the server's exit or the non-protocol line must be reported.
        assert "exited" in result.error or "non-protocol" in result.error
        assert "junk" not in client._initialized
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_server_dying_after_initialize_fails_cleanly(tmp_path):
    script = _write_script(tmp_path, "fake_die.py", FAKE_DIE_AFTER_INIT_SERVER)
    client = MCPClient()
    try:
        client.add_server(MCPServerConfig(name="die", command=sys.executable, args=[str(script)]))
        result = await client.call_tool("die/echo", {})
        assert result.success is False
        assert "die" in result.error
        assert "exited" in result.error or "disconnected" in result.error
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_hung_server_times_out(tmp_path):
    script = _write_script(tmp_path, "fake_hung.py", FAKE_HUNG_SERVER)
    client = MCPClient(request_timeout=0.5)
    try:
        client.add_server(MCPServerConfig(name="hung", command=sys.executable, args=[str(script)]))
        result = await client.call_tool("hung/echo", {})
        assert result.success is False
        assert "timed out" in result.error
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stderr_is_drained_and_kept(tmp_path):
    """A chatty server must not fill the stderr pipe and block forever."""
    script = _write_script(tmp_path, "fake_noisy.py", FAKE_NOISY_SERVER)
    client = MCPClient(request_timeout=0.5)
    try:
        client.add_server(MCPServerConfig(name="noisy", command=sys.executable, args=[str(script)]))
        with pytest.raises(MCPServerError):
            await client.initialize_server("noisy")  # times out: server never answers
        for _ in range(100):
            if client._stderr_tails.get("noisy"):
                break
            await asyncio.sleep(0.02)
        assert "noise for the stderr tail" in " ".join(client._stderr_tails.get("noisy", []))
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_failed_server_leaves_no_process_behind(tmp_path):
    script = _write_script(tmp_path, "fake_die.py", FAKE_DIE_AFTER_INIT_SERVER)
    client = MCPClient()
    client.add_server(MCPServerConfig(name="die", command=sys.executable, args=[str(script)]))
    await client.call_tool("die/echo", {})
    assert "die" not in client._processes


# ── adapter mapping + registration ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_adapter_maps_mcp_tools_to_nexus_tool(tmp_path):
    client = MCPClient()
    try:
        client.add_server(_ok_config(tmp_path))
        adapter_cls = mcp_tool_from_server(client, "fake")
        adapter = adapter_cls()

        definition = adapter.definition
        assert definition.name == "mcp_fake"
        assert definition.category == "mcp"
        assert definition.requires_permission is True
        # Not yet connected: tools are described as discovered on first use.
        assert "discovered on first use" in definition.description

        await client.initialize_server("fake")
        definition = adapter.definition
        assert "echo" in definition.description and "add" in definition.description
        assert "required args: message" in definition.description

        result = await adapter.execute(tool="echo", args={"message": "hi"})
        assert result.success is True
        assert result.content == "echo: hi"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_adapter_validates_arguments(tmp_path):
    client = MCPClient()
    try:
        client.add_server(_ok_config(tmp_path))
        adapter = mcp_tool_from_server(client, "fake")()
        missing = await adapter.execute(args={"message": "hi"})
        assert missing.success is False and "No tool specified" in missing.error

        bad_json = await adapter.execute(tool="echo", args="not json")
        assert bad_json.success is False and "must be a JSON object" in bad_json.error

        # A JSON-encoded args string is accepted (models sometimes send this).
        encoded = await adapter.execute(tool="echo", args=json.dumps({"message": "ok"}))
        assert encoded.success is True and encoded.content == "echo: ok"
    finally:
        await client.close()


def test_register_mcp_tools_and_call_through_registry(isolated_mcp_config, tmp_path):
    script = _write_script(tmp_path, "fake_ok_server.py", FAKE_OK_SERVER)
    isolated_mcp_config.write_text(
        json.dumps({"fake": {"command": sys.executable, "args": [str(script)]}}),
        encoding="utf-8",
    )
    registry = ToolRegistry()

    registered = register_mcp_tools(registry)
    assert registered == ["mcp_fake"]
    adapter = registry.get("mcp_fake")
    assert adapter is not None
    assert "fake" in adapter.definition.description

    result = asyncio.run(adapter.execute(tool="echo", args={"message": "hi"}))
    assert result.success is True
    assert result.content == "echo: hi"

    # After first use the adapter description shows the real tools.
    assert "echo" in adapter.definition.description


def test_registration_is_idempotent(isolated_mcp_config, tmp_path):
    script = _write_script(tmp_path, "fake_ok_server.py", FAKE_OK_SERVER)
    isolated_mcp_config.write_text(
        json.dumps({"fake": {"command": sys.executable, "args": [str(script)]}}),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    assert register_mcp_tools(registry) == ["mcp_fake"]
    assert register_mcp_tools(registry) == []
    assert len(registry.list_by_category("mcp")) == 1


def test_get_registry_includes_mcp_adapters(monkeypatch, tmp_path):
    """The agent-facing global registry must expose installed MCP servers."""
    script = _write_script(tmp_path, "fake_ok_server.py", FAKE_OK_SERVER)
    config_path = tmp_path / "servers.json"
    config_path.write_text(
        json.dumps({"fake": {"command": sys.executable, "args": [str(script)]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXUS_MCP_CONFIG", str(config_path))
    monkeypatch.setattr(mcp_module, "_client", None)
    monkeypatch.setattr("nexus.tools.base._registry", None)

    from nexus.tools import get_registry

    registry = get_registry()
    names = {d.name for d in registry.list_all()}
    assert "mcp_fake" in names
    assert "bash" in names  # core tools untouched


def test_broken_server_does_not_break_the_rest(isolated_mcp_config, tmp_path):
    good_script = _write_script(tmp_path, "fake_ok_server.py", FAKE_OK_SERVER)
    isolated_mcp_config.write_text(
        json.dumps(
            {
                "good": {"command": sys.executable, "args": [str(good_script)]},
                "broken": {"command": "nexus-definitely-missing-cmd-xyz"},
            }
        ),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    assert register_mcp_tools(registry) == ["mcp_good", "mcp_broken"]

    broken = asyncio.run(registry.get("mcp_broken").execute(tool="echo"))
    assert broken.success is False
    assert "command not found" in broken.error

    good = asyncio.run(registry.get("mcp_good").execute(tool="echo", args={"message": "hi"}))
    assert good.success is True
    assert good.content == "echo: hi"


def test_missing_config_is_a_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_MCP_CONFIG", str(tmp_path / "does-not-exist.json"))
    monkeypatch.setattr(mcp_module, "_client", None)
    registry = ToolRegistry()
    assert register_mcp_tools(registry) == []
    assert registry.list_by_category("mcp") == []


@pytest.mark.parametrize(
    "content",
    [
        "this is not json at all",
        "[]",  # JSON but not an object
        json.dumps({"bad": "just a string"}),
        json.dumps({"bad": [1, 2]}),
    ],
)
def test_malformed_config_is_a_noop(isolated_mcp_config, content):
    isolated_mcp_config.write_text(content, encoding="utf-8")
    registry = ToolRegistry()
    assert register_mcp_tools(registry) == []
    assert registry.list_by_category("mcp") == []


def test_load_server_configs_normalizes_and_filters(isolated_mcp_config):
    isolated_mcp_config.write_text(
        json.dumps(
            {
                "str_args": {"command": "ls", "args": "-la /tmp"},
                "sse": {"transport": "sse", "url": "http://localhost:9", "extra": "dropped"},
            }
        ),
        encoding="utf-8",
    )
    configs, errors = load_server_configs(isolated_mcp_config)
    assert errors == {}
    assert configs["str_args"].args == ["-la", "/tmp"]
    sse = configs["sse"]
    assert sse.command == ""  # SSE entries have no command; must not TypeError
    assert sse.url == "http://localhost:9"
    assert not hasattr(sse, "extra")


def test_default_config_path_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_MCP_CONFIG", str(tmp_path / "x.json"))
    assert default_config_path() == tmp_path / "x.json"
    monkeypatch.delenv("NEXUS_MCP_CONFIG")
    assert default_config_path() == Path.home() / ".nexus" / "mcp-servers.json"


# ── status reporting (feeds `nexus mcp list`) ──────────────────────────────


def test_status_config_only(isolated_mcp_config):
    isolated_mcp_config.write_text(
        json.dumps(
            {
                "alpha": {"command": "echo", "args": ["hi"]},
                "beta": {"transport": "sse", "url": "http://localhost:9"},
                "bad": "not an object",
            }
        ),
        encoding="utf-8",
    )
    status = {entry["name"]: entry for entry in collect_server_status()}
    assert status["alpha"]["target"] == "echo"
    assert status["alpha"]["connected"] is False
    assert status["beta"]["transport"] == "sse"
    assert status["beta"]["target"] == "http://localhost:9"
    assert "must be a JSON object" in status["bad"]["error"]


def test_status_connect_reports_reality(isolated_mcp_config, tmp_path):
    good_script = _write_script(tmp_path, "fake_ok_server.py", FAKE_OK_SERVER)
    isolated_mcp_config.write_text(
        json.dumps(
            {
                "good": {"command": sys.executable, "args": [str(good_script)]},
                "ghost": {"command": "nexus-definitely-missing-cmd-xyz"},
            }
        ),
        encoding="utf-8",
    )
    status = {entry["name"]: entry for entry in collect_server_status(connect=True)}
    good = status["good"]
    assert good["connected"] is True
    assert good["tool_count"] == 3
    assert set(good["tools"]) == {"echo", "add", "fail"}
    assert good["error"] == ""

    ghost = status["ghost"]
    assert ghost["connected"] is False
    assert "command not found" in ghost["error"]
