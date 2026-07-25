"""
Unit Test Suite for Plug-In / Plug-Out Adapters & Global Tool Gateway Router (POST /mcp):
- Plug-In Adapter Lifecycle (/adapters CRUD)
- Adapter Status Tracking & Log Inspection (/adapters/{name}/status, /adapters/{name}/logs)
- Global Tool Gateway Router (POST /mcp tools/list & tools/call)
- System-Wide Specialized Mock Features (Chaos Injection & Trace Recording) through Central Router
"""

import pytest
from starlette.testclient import TestClient

from mcp_gateway.adapters import AdapterStatus, AdapterType, PlugInAdapterConfig, adapter_registry
from mcp_gateway.chaos import chaos_engine
from mcp_gateway.manager import ServerManager
from mcp_gateway.recorder import trace_recorder


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensure clean adapter registry state for each test."""
    for adapter in list(adapter_registry._adapters.keys()):
        adapter_registry.unregister_adapter(adapter)
    chaos_engine.clear_rules()


def test_plugin_adapter_control_plane_crud():
    """Test /adapters CRUD REST APIs for dynamic plug-in and plug-out of MCP servers."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {"X-API-Key": "admin-secret-key-123"}

    # 1. GET /adapters -> Should list loaded scenarios as plugged-in adapters
    res = client.get("/adapters", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["count"] >= 3

    # 2. POST /adapters -> Plug-in a custom mock adapter
    new_adapter = {
        "name": "custom-analytics-mcp",
        "description": "Custom plugged-in analytics mock server",
        "adapter_type": "mock_local",
        "tools": ["calculate", "generate_random_data"],
        "status": "Running",
    }
    plug_res = client.post("/adapters", json=new_adapter, headers=auth_headers)
    assert plug_res.status_code == 201
    assert plug_res.json()["status"] == "plugged_in"
    assert plug_res.json()["adapter"]["name"] == "custom-analytics-mcp"

    # 3. GET /adapters/custom-analytics-mcp/status
    status_res = client.get("/adapters/custom-analytics-mcp/status", headers=auth_headers)
    assert status_res.status_code == 200
    assert status_res.json()["status"] == "Running"

    # 4. PUT /adapters/custom-analytics-mcp -> Pause adapter
    update_res = client.put(
        "/adapters/custom-analytics-mcp",
        json={"status": "Paused", "reason": "Scheduled maintenance"},
        headers=auth_headers,
    )
    assert update_res.status_code == 200
    assert update_res.json()["adapter"]["status"] == "Paused"

    # 5. GET /adapters/custom-analytics-mcp/logs
    logs_res = client.get("/adapters/custom-analytics-mcp/logs", headers=auth_headers)
    assert logs_res.status_code == 200
    assert logs_res.json()["count"] > 0

    # 6. DELETE /adapters/custom-analytics-mcp -> Plug-out adapter
    del_res = client.delete("/adapters/custom-analytics-mcp", headers=auth_headers)
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "plugged_out"


def test_global_tool_gateway_router():
    """Test central POST /mcp endpoint for tools/list, tools/call, and transparent mock features."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "X-Session-ID": "router-test-sess",
        "X-MCP-Scenario": "full-scenario",
    }

    # 1. POST /mcp initialize
    init_res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers=auth_headers,
    )
    assert init_res.status_code == 200
    assert init_res.json()["result"]["serverInfo"]["name"] == "Global Tool Gateway Router"

    # 2. POST /mcp tools/list -> Aggregates tools across running plugged-in adapters
    list_res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        headers=auth_headers,
    )
    assert list_res.status_code == 200
    tools = list_res.json()["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "calculate" in tool_names
    assert "manage_users" in tool_names

    # 3. POST /mcp tools/call (calculate)
    call_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "calculate", "arguments": {"expression": "50 * 4"}},
        },
        headers=auth_headers,
    )
    assert call_res.status_code == 200
    res_data = call_res.json()["result"]
    assert res_data["isError"] is False
    assert res_data["structuredContent"]["result"] == 200

    # 4. SYSTEM-WIDE SPECIALIZED MOCK FEATURE: Inject Chaos Fault on central router call
    client.post(
        "/scenarios/full-scenario/chaos",
        json={
            "tool_name": "calculate",
            "fault_type": "error_rate",
            "rate": 1.0,
            "error_message": "Router Injected Fault Test",
        },
        headers=auth_headers,
    )

    faulted_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "calculate", "arguments": {"expression": "50 * 4"}},
        },
        headers=auth_headers,
    )
    assert faulted_res.status_code == 200
    assert faulted_res.json()["result"]["isError"] is True
    assert "Router Injected Fault Test" in faulted_res.json()["result"]["content"][0]["text"]


def test_mcp_filtering_features():
    """Test filtering tools and calling tools targeting specific MCP servers/adapters."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {"X-API-Key": "admin-secret-key-123"}

    # 1. Test GET /adapters/{name}/tools
    tools_res = client.get("/adapters/utility-scenario/tools", headers=auth_headers)
    assert tools_res.status_code == 200
    data = tools_res.json()
    assert data["status"] == "success"
    assert data["adapter_name"] == "utility-scenario"
    tool_names = [t["name"] for t in data["tools"]]
    assert "calculate" in tool_names

    # 2. Test POST /mcp tools/list with specific servers filter
    list_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {"servers": ["utility-scenario"]},
        },
        headers=auth_headers,
    )
    assert list_res.status_code == 200
    filtered_tools = list_res.json()["result"]["tools"]
    filtered_names = [t["name"] for t in filtered_tools]
    assert "calculate" in filtered_names
    assert "get_system_info" not in filtered_names  # belongs to sysops-scenario

    # 3. Test POST /mcp tools/call with explicit server targeting
    call_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "calculate",
                "arguments": {"expression": "10 + 20"},
                "server": "utility-scenario",
            },
        },
        headers=auth_headers,
    )
    assert call_res.status_code == 200
    assert call_res.json()["result"]["structuredContent"]["result"] == 30

    # 4. Test find_tools meta-tool with mcp_filter
    from mcp_gateway.tools.discovery_tools import find_tools
    search_res = find_tools(query="math calculation", top_k=2, mcp_filter=["utility-scenario"])
    assert search_res["matches_found"] > 0
    assert search_res["mcp_filter"] == ["utility-scenario"]


def test_duplicate_tool_name_handling():
    """Test duplicate tool name collision detection, namespace qualification (adapter::tool), and explicit routing."""
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    client = TestClient(app)

    auth_headers = {"X-API-Key": "admin-secret-key-123"}

    # 1. Register two adapters both providing tool 'echo'
    adapter_registry.register_adapter(
        PlugInAdapterConfig(name="mock-alpha", adapter_type=AdapterType.MOCK_LOCAL, tools=["echo"], status=AdapterStatus.RUNNING)
    )
    adapter_registry.register_adapter(
        PlugInAdapterConfig(name="mock-beta", adapter_type=AdapterType.MOCK_LOCAL, tools=["echo"], status=AdapterStatus.RUNNING)
    )

    # 2. Test tools/list exposes namespace qualified tool names
    list_res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=auth_headers,
    )
    assert list_res.status_code == 200
    tools = list_res.json()["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "echo" in tool_names
    assert "mock-alpha::echo" in tool_names
    assert "mock-beta::echo" in tool_names

    # 3. Test ambiguous call without specifying server -> should return collision error
    ambig_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hello"}},
        },
        headers=auth_headers,
    )
    assert ambig_res.status_code == 400
    assert "Ambiguous tool name collision" in ambig_res.json()["error"]["message"]

    # 4. Test explicit server targeting in params
    explicit_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hello alpha"}, "server": "mock-alpha"},
        },
        headers=auth_headers,
    )
    assert explicit_res.status_code == 200
    assert explicit_res.json()["result"]["structuredContent"]["original_message"] == "hello alpha"

    # 5. Test fully-qualified name calling (adapter::tool)
    qual_res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "mock-beta::echo", "arguments": {"message": "hello beta"}},
        },
        headers=auth_headers,
    )
    assert qual_res.status_code == 200
    assert qual_res.json()["result"]["structuredContent"]["original_message"] == "hello beta"


