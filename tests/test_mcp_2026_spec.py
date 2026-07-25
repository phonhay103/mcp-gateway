"""
Unit Test Suite for MCP 2026-07-28 Specification Adoption:
- Protocol Version negotiation ("2026-07-28")
- Stateless Core capabilities & extensions (mcp-apps, tasks)
- Tools list caching fields (ttlMs, cacheScope)
- Mcp-Method and Mcp-Name header enforcement (Streamable HTTP)
- Tasks extension handlers (tasks/get, tasks/update)
- Server discover endpoint (server/discover)
- Native mcp.types.Tool definition support
"""

import pytest
from starlette.testclient import TestClient
from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.core.schemas import ToolDefinition


def test_mcp_2026_07_28_initialization_and_capabilities():
    """Test that initialize returns MCP 2026-07-28 protocol version, stateless capabilities, and extensions."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "X-Session-ID": "spec-2026-test",
        "Mcp-Method": "initialize",
    }

    init_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2026-07-28",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0.0"},
            },
        },
        headers=auth_headers,
    )
    assert init_res.status_code == 200
    res_data = init_res.json()["result"]
    
    assert res_data["protocolVersion"] == "2026-07-28"
    caps = res_data["capabilities"]
    assert caps.get("statelessCore") is True
    assert "extensions" in caps
    assert caps["extensions"]["mcp-apps"]["supported"] is True
    assert caps["extensions"]["tasks"]["supported"] is True
    assert res_data["serverInfo"]["version"] == "2026.07.28"


def test_server_discover():
    """Test server/discover stateless method."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "X-Session-ID": "spec-2026-test",
        "Mcp-Method": "server/discover",
    }

    res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 10, "method": "server/discover", "params": {}},
        headers=auth_headers,
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["protocolVersion"] == "2026-07-28"
    assert result["capabilities"]["statelessCore"] is True


def test_mcp_2026_07_28_tools_list_caching_metadata():
    """Test that tools/list responses include caching metadata (ttlMs, cacheScope) per MCP 2026-07-28."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "X-Session-ID": "spec-2026-test",
        "Mcp-Method": "tools/list",
    }

    list_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        },
        headers=auth_headers,
    )
    assert list_res.status_code == 200
    result = list_res.json()["result"]

    assert "tools" in result
    assert result.get("ttlMs") == 30000
    assert result.get("cacheScope") == "user"


def test_mcp_2026_header_enforcement():
    """Test Mcp-Method header enforcement (disagreement between header and body rejects request)."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "X-Session-ID": "spec-2026-test",
        "Mcp-Method": "tools/list",  # Header says tools/list
    }

    # Body says initialize -> should be rejected with 400
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "initialize",
            "params": {},
        },
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "does not match" in res.json()["error"]["message"]


def test_mcp_name_header_enforcement():
    """Test Mcp-Name header enforcement on tools/call."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "X-Session-ID": "spec-2026-test",
        "Mcp-Method": "tools/call",
        "Mcp-Name": "echo",  # Header says echo
    }

    # Body calls calculate -> should be rejected with 400
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "calculate", "arguments": {"expression": "1+1"}},
        },
        headers=auth_headers,
    )
    assert res.status_code == 400
    assert "Mcp-Name" in res.json()["error"]["message"]


def test_mcp_2026_tasks_extension():
    """Test tasks/get and tasks/update extension method execution."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "X-Session-ID": "spec-2026-test",
        "Mcp-Method": "tasks/get",
    }

    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tasks/get",
            "params": {"taskId": "task-123"},
        },
        headers=auth_headers,
    )
    assert res.status_code == 200
    result = res.json()["result"]
    assert result["taskId"] == "task-123"
    assert result["status"] == "completed"

    # Test tasks/update
    res_update = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tasks/update",
            "params": {"taskId": "task-123", "inputResponses": {"answer": "yes"}},
        },
        headers={"X-API-Key": "admin-secret-key-123", "Mcp-Method": "tasks/update"},
    )
    assert res_update.status_code == 200
    assert res_update.json()["result"]["status"] == "completed"


def test_native_mcp_tool_definition():
    """Test ToolDefinition subclassing native mcp.types.Tool."""
    tool = ToolDefinition(
        name="native_tool",
        description="A native MCP tool",
        inputSchema={"type": "object", "properties": {"x": {"type": "integer"}}}
    )
    assert tool.name == "native_tool"
    mcp_fmt = tool.to_mcp_format()
    assert mcp_fmt["name"] == "native_tool"
    assert "inputSchema" in mcp_fmt
