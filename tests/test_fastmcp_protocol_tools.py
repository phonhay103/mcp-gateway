"""
Tests verifying native FastMCP tools/list MCP Protocol serving and MCP isError tool error simulation.
Validates that FastMCP returns complete tool definitions with JSON inputSchemas
directly through the official MCP protocol (FastMCP.list_tools()) and handles isError: True correctly.
"""

import pytest
from mock_mcp_gateway.server import mcp
from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.session import current_scenario_id, current_server_id, current_session_id, session_manager
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

    # 2. Utility Scenario Server Instance
    utility_mcp = manager.instances["utility-scenario"]
    utility_tools = await utility_mcp.list_tools()
    utility_tool_names = [t.name for t in utility_tools]

    assert "calculate" in utility_tool_names
    assert "manage_users" in utility_tool_names
    assert "generate_random_data" in utility_tool_names


@pytest.mark.anyio
async def test_fastmcp_scenario_tool_error_simulation_is_error():
    """Test MCP tool error simulations returning isError: True per MCP spec."""
    manager = ServerManager(Path("config/mcp_servers.yaml"))
    full_mcp = manager.instances["full-scenario"]

    # 1. Test error simulation under SysOps Scenario for 'calculate'
    tok_sess1 = current_session_id.set("test-err-sess")
    tok_scen1 = current_scenario_id.set("sysops-scenario")
    tok_srv1 = current_server_id.set("full-scenario")
    try:
        session_manager.get_or_create_session(
            session_id="test-err-sess",
            scenario_id="sysops-scenario",
            server_id="full-scenario",
            initial_state=manager.scenario_fixture_states.get("sysops-scenario"),
        )

        res_sysops_err = await full_mcp.call_tool("calculate", {"expression": "5 + 5"})
        assert res_sysops_err.is_error is True
        assert "restricted in infrastructure monitoring mode" in res_sysops_err.content[0].text
    finally:
        current_session_id.reset(tok_sess1)
        current_scenario_id.reset(tok_scen1)
        current_server_id.reset(tok_srv1)

    # 2. Test error simulation under Utility Scenario for 'http_ping'
    tok_sess2 = current_session_id.set("test-err-sess-2")
    tok_scen2 = current_scenario_id.set("utility-scenario")
    tok_srv2 = current_server_id.set("full-scenario")
    try:
        session_manager.get_or_create_session(
            session_id="test-err-sess-2",
            scenario_id="utility-scenario",
            server_id="full-scenario",
            initial_state=manager.scenario_fixture_states.get("utility-scenario"),
        )

        res_util_err = await full_mcp.call_tool("http_ping", {"url": "https://example.com"})
        assert res_util_err.is_error is True
        assert "Outbound network ping is prohibited in data utility mode" in res_util_err.content[0].text
    finally:
        current_session_id.reset(tok_sess2)
        current_scenario_id.reset(tok_scen2)
        current_server_id.reset(tok_srv2)

    # 3. Test normal execution without error simulation in full-scenario
    tok_sess3 = current_session_id.set("test-normal-sess")
    tok_scen3 = current_scenario_id.set("full-scenario")
    tok_srv3 = current_server_id.set("full-scenario")
    try:
        session_manager.get_or_create_session(
            session_id="test-normal-sess",
            scenario_id="full-scenario",
            server_id="full-scenario",
            initial_state=manager.scenario_fixture_states.get("full-scenario"),
        )

        res_ok = await full_mcp.call_tool("calculate", {"expression": "5 + 5"})
        assert res_ok.is_error is False

        # 4. Test native runtime execution error returns isError: True
        res_runtime_err = await full_mcp.call_tool("calculate", {"expression": "1 / 0"})
        assert res_runtime_err.is_error is True
        assert "division by zero" in res_runtime_err.content[0].text
    finally:
        current_session_id.reset(tok_sess3)
        current_scenario_id.reset(tok_scen3)
        current_server_id.reset(tok_srv3)
