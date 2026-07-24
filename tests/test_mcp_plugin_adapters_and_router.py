"""
Unit Test Suite for Plug-In / Plug-Out Adapters & Global Tool Gateway Router (POST /mcp):
- Plug-In Adapter Lifecycle (/adapters CRUD)
- Adapter Status Tracking & Log Inspection (/adapters/{name}/status, /adapters/{name}/logs)
- Global Tool Gateway Router (POST /mcp tools/list & tools/call)
- System-Wide Specialized Mock Features (Chaos Injection & Trace Recording) through Central Router
"""

import pytest
from starlette.testclient import TestClient

from mock_mcp_gateway.adapters import AdapterStatus, AdapterType, PlugInAdapterConfig, adapter_registry
from mock_mcp_gateway.chaos import chaos_engine
from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.recorder import trace_recorder


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
