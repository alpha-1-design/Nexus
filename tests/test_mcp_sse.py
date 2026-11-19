"""Hermetic tests for the MCP SSE transport (stdlib-only client).

A threading http.server on an ephemeral localhost port plays the MCP server:
GET /sse announces the POST endpoint, then JSON-RPC flows over POSTs.
"""

import http.server
import json
import threading

import pytest

from nexus.mcp import MCPClient, MCPServerConfig, MCPServerError


class FakeMCPHandler(http.server.BaseHTTPRequestHandler):
    """Minimal fake MCP-over-SSE server."""

    server_version = "FakeMCP/1.0"
    broken = False  # 500 on GET /sse
    no_endpoint = False  # SSE stream without an endpoint event

    def log_message(self, *args):
        pass

    def _send(self, status: int, obj: dict | None = None) -> None:
        data = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def do_GET(self):
        if not self.path.rstrip("/").endswith("/sse"):
            self._send(404, {"error": "not found"})
            return
        if self.broken:
            self._send(500, {"error": "boom"})
            return
        if self.no_endpoint:
            body = "keepalive: ping\n\n"
        else:
            body = "event: endpoint\ndata: /messages?session=abc123\n\n"
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"null")
        except json.JSONDecodeError:
            self._send(400, {"error": "bad json"})
            return
        if not isinstance(payload, dict) or "id" not in payload:
            self._send(202)  # notification: no body
            return
        self._send(200, self._respond(payload))

    def _respond(self, req: dict) -> dict:
        method = req.get("method")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake", "version": "1.0"},
                },
            }
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "tools": [
                        {
                            "name": "echo",
                            "description": "Echo the given text",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                        },
                        {
                            "name": "add",
                            "description": "Always fails server-side",
                            "inputSchema": {"type": "object"},
                        },
                    ]
                },
            }
        if method == "tools/call":
            name = req.get("params", {}).get("name")
            if name == "echo":
                text = req.get("params", {}).get("arguments", {}).get("text", "")
                return {
                    "jsonrpc": "2.0",
                    "id": req["id"],
                    "result": {
                        "content": [{"type": "text", "text": f"echo: {text}"}],
                        "isError": False,
                    },
                }
            return {
                "jsonrpc": "2.0",
                "id": req["id"],
                "error": {"code": -32602, "message": f"tool '{name}' exploded"},
            }
        return {
            "jsonrpc": "2.0",
            "id": req["id"],
            "error": {"code": -32601, "message": "method not found"},
        }


def _start_server(handler_cls=FakeMCPHandler):
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd, url


@pytest.fixture
def fake_server():
    httpd, url = _start_server()
    yield url
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.asyncio
async def test_sse_handshake_discovers_tools(fake_server):
    client = MCPClient(request_timeout=5)
    client.add_server(MCPServerConfig(name="fake", url=fake_server, transport="sse"))
    await client.initialize_server("fake")

    tools = {t.name for t in client.list_tools() if t.server_name == "fake"}
    assert tools == {"echo", "add"}
    echo = client.get_tool("fake/echo")
    assert echo is not None and echo.input_schema["required"] == ["text"]
    await client.close()


@pytest.mark.asyncio
async def test_sse_tool_call_round_trip(fake_server):
    client = MCPClient(request_timeout=5)
    client.add_server(MCPServerConfig(name="fake", url=fake_server, transport="sse"))

    # initialize_server is lazy inside call_tool — a full round trip in one go.
    result = await client.call_tool("fake/echo", {"text": "hi"})
    assert result.success
    assert result.content == "echo: hi"
    await client.close()


@pytest.mark.asyncio
async def test_sse_jsonrpc_error_maps_to_failed_result(fake_server):
    client = MCPClient(request_timeout=5)
    client.add_server(MCPServerConfig(name="fake", url=fake_server, transport="sse"))

    result = await client.call_tool("fake/add", {})
    assert not result.success
    assert "exploded" in (result.error or "")
    await client.close()


@pytest.mark.asyncio
async def test_sse_unknown_tool_reports_available(fake_server):
    client = MCPClient(request_timeout=5)
    client.add_server(MCPServerConfig(name="fake", url=fake_server, transport="sse"))

    result = await client.call_tool("fake/nope", {})
    assert not result.success
    assert "Available tools: add, echo" in (result.error or "")
    await client.close()


@pytest.mark.asyncio
async def test_sse_broken_server_degrades_gracefully():
    class Broken(FakeMCPHandler):
        broken = True

    httpd, url = _start_server(Broken)
    try:
        client = MCPClient(request_timeout=5)
        client.add_server(MCPServerConfig(name="broken", url=url, transport="sse"))
        result = await client.call_tool("broken/echo", {"text": "x"})
        assert not result.success
        assert "500" in (result.error or "")
        await client.close()
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.asyncio
async def test_sse_stream_without_endpoint_event_raises():
    class NoEndpoint(FakeMCPHandler):
        no_endpoint = True

    httpd, url = _start_server(NoEndpoint)
    try:
        client = MCPClient(request_timeout=5)
        client.add_server(MCPServerConfig(name="bare", url=url, transport="sse"))
        with pytest.raises(MCPServerError, match="endpoint"):
            await client.initialize_server("bare")
        await client.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
