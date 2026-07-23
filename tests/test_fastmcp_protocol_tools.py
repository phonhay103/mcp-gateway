"""
Tests verifying native FastMCP tools/list MCP Protocol serving.
Validates that FastMCP returns complete tool definitions with JSON inputSchemas
directly through the official MCP protocol (FastMCP.list_tools()).
"""

import pytest
from mock_mcp_gateway.server import mcp
from mock_mcp_gateway.manager import ServerManager
from pathlib import Path


@pytest.mark.anyio
async def test_fastmcp_native_list_tools():
    """Test FastMCP's native list_tools() protocol output for default server."""
    tools = await mcp.list_tools()
    assert len(tools) >= 6

    tool_map = {t.name: t for t in tools}
    assert "echo" in tool_map
    assert "manage_users" in tool_map
    assert "calculate" in tool_map

    # Inspect native MCP tool schema for manage_users
    mcp_tool = tool_map["manage_users"].to_mcp_tool()
    assert mcp_tool.name == "manage_users"
    assert mcp_tool.description is not None
    assert "inputSchema" in mcp_tool.model_dump()

    schema = mcp_tool.inputSchema
    assert schema["type"] == "object"
    assert "action" in schema["properties"]
    assert "user_id" in schema["properties"]
    assert "action" in schema["required"]
    assert schema["properties"]["action"]["description"] == "User CRUD action ('list', 'get', 'create', 'delete')"


@pytest.mark.anyio
async def test_fastmcp_scenario_native_list_tools():
    """Test FastMCP's native list_tools() protocol output for individual scenario instances."""
    manager = ServerManager(Path("config/mcp_servers.yaml"))

    # 1. SysOps Scenario Server Instance
    sysops_mcp = manager.instances["sysops-scenario"]
    sysops_tools = await sysops_mcp.list_tools()
    sysops_tool_names = [t.name for t in sysops_tools]

    assert "get_system_info" in sysops_tool_names
    assert "http_ping" in sysops_tool_names
    assert "calculate" not in sysops_tool_names

    # 2. Utility Scenario Server Instance
    utility_mcp = manager.instances["utility-scenario"]
    utility_tools = await utility_mcp.list_tools()
    utility_tool_names = [t.name for t in utility_tools]

    assert "calculate" in utility_tool_names
    assert "manage_users" in utility_tool_names
    assert "generate_random_data" in utility_tool_names
    assert "get_system_info" not in utility_tool_names
