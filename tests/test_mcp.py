"""Tests for the MCP (Model Context Protocol) client."""

import json
import os
import tempfile

import pytest

from nexus.mcp import MCPServerConfig, MCPTool


class TestMCPServerConfig:
    """MCPServerConfig construction and defaults."""

    def test_stdio_config(self):
        cfg = MCPServerConfig(name="test", command="node", args=["server.js"])
        assert cfg.name == "test"
        assert cfg.command == "node"
        assert cfg.args == ["server.js"]
        assert cfg.transport == "stdio"
        assert cfg.auto_load is True

    def test_sse_config(self):
        cfg = MCPServerConfig(
            name="remote", command="", url="http://localhost:3000", transport="sse"
        )
        assert cfg.name == "remote"
        assert cfg.url == "http://localhost:3000"
        assert cfg.transport == "sse"

    def test_config_with_env(self):
        cfg = MCPServerConfig(
            name="env-test",
            command="python",
            env={"MY_KEY": "value"},
        )
        assert cfg.env == {"MY_KEY": "value"}


class TestMCPTool:
    """MCPTool dataclass."""

    def test_tool_creation(self):
        tool = MCPTool(
            name="get_weather",
            description="Get weather data",
            input_schema={"type": "object", "properties": {}},
            server_name="weather-server",
        )
        assert tool.name == "get_weather"
        assert tool.server_name == "weather-server"
        assert tool.description == "Get weather data"

    def test_tool_full_name(self):
        tool = MCPTool(
            name="search",
            description="Search tool",
            input_schema={},
            server_name="search-server",
        )
        full = f"{tool.server_name}/{tool.name}"
        assert full == "search-server/search"
