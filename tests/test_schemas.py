"""
Unit tests for Tool Schema Registry and Tool Schema API Endpoints.
Verifies loading JSON schemas, converting formats (MCP vs OpenAI), and API endpoints:
- GET /tools
- GET /tools/{tool_name}
- GET /scenarios/{scenario_id}/tools
"""

from pathlib import Path
import pytest
from starlette.testclient import TestClient

from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.schemas import ToolDefinition, ToolSchemaRegistry, schema_registry


@pytest.fixture
def gateway_client():
    """Fixture providing a TestClient for a configured ServerManager Gateway app."""
    config_file = Path("config/mcp_servers.yaml")
    manager = ServerManager(config_file)
    app = manager.create_gateway_app()
    return TestClient(app)


def test_schema_registry_loading():
    """Test loading tool JSON schemas directly via ToolSchemaRegistry."""
    registry = ToolSchemaRegistry(
        schemas_dir=Path("config/schemas/tools"),
        catalog_file=Path("config/schemas/tools.json"),
    )
    assert len(registry.tools) >= 6
    assert "echo" in registry.tools
    assert "calculate" in registry.tools
    assert "manage_users" in registry.tools
    assert "http_ping" in registry.tools
    assert "generate_random_data" in registry.tools
    assert "get_system_info" in registry.tools


def test_tool_definition_formats():
    """Test converting ToolDefinition to MCP and OpenAI formats."""
    tool_def = schema_registry.get_tool("echo")
    assert tool_def is not None

    mcp_fmt = tool_def.to_mcp_format()
    assert mcp_fmt["name"] == "echo"
    assert "inputSchema" in mcp_fmt
    assert "message" in mcp_fmt["inputSchema"]["properties"]

    openai_fmt = tool_def.to_openai_format()
    assert openai_fmt["type"] == "function"
    assert openai_fmt["function"]["name"] == "echo"
    assert "parameters" in openai_fmt["function"]


def test_list_tools_endpoint(gateway_client: TestClient):
    """Test GET /tools endpoint."""
    res = gateway_client.get("/tools")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert data["format"] == "mcp"
    assert data["count"] >= 6
    assert isinstance(data["tools"], list)

    tool_names = [t["name"] for t in data["tools"]]
    assert "echo" in tool_names
    assert "manage_users" in tool_names


def test_list_tools_openai_format(gateway_client: TestClient):
    """Test GET /tools?format=openai endpoint."""
    res = gateway_client.get("/tools?format=openai")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert data["format"] == "openai"
    first_tool = data["tools"][0]
    assert first_tool["type"] == "function"
    assert "function" in first_tool
    assert "name" in first_tool["function"]


def test_get_individual_tool_schema(gateway_client: TestClient):
    """Test GET /tools/{tool_name} endpoint."""
    res = gateway_client.get("/tools/manage_users")
    assert res.status_code == 200
    data = res.json()

    assert data["name"] == "manage_users"
    assert "inputSchema" in data
    assert "action" in data["inputSchema"]["properties"]
    assert "action" in data["inputSchema"]["required"]


def test_get_nonexistent_tool_schema(gateway_client: TestClient):
    """Test GET /tools/{tool_name} with non-existent tool name."""
    res = gateway_client.get("/tools/non_existent_tool_123")
    assert res.status_code == 404
    assert "not found" in res.json()["message"]


def test_scenario_tools_schema_endpoint(gateway_client: TestClient):
    """Test GET /scenarios/{scenario_id}/tools endpoint."""
    # 1. SysOps scenario
    res_sysops = gateway_client.get("/scenarios/sysops-scenario/tools")
    assert res_sysops.status_code == 200
    data_sysops = res_sysops.json()

    assert data_sysops["status"] == "success"
    assert data_sysops["scenario_id"] == "sysops-scenario"
    sysops_tools = [t["name"] for t in data_sysops["tools"]]
    assert "get_system_info" in sysops_tools
    assert "http_ping" in sysops_tools
    assert "calculate" not in sysops_tools

    # 2. Full scenario
    res_full = gateway_client.get("/scenarios/full-scenario/tools")
    assert res_full.status_code == 200
    data_full = res_full.json()
    full_tools = [t["name"] for t in data_full["tools"]]
    assert "manage_users" in full_tools
    assert "calculate" in full_tools
    assert "get_system_info" in full_tools
