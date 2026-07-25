"""
Integration Test Suite for Refactored Architecture:
- Middleware pipeline (Header validation, Chaos injection)
- MCP 2026-07-28 Extensions (Tasks & MCP Apps)
- Stateful Scenarios & Fixtures
"""

import pytest
from starlette.testclient import TestClient
from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.core.extensions.mcp_extensions import task_manager


@pytest.fixture
def refactored_client():
    manager = ServerManager("config/mcp_servers.yaml")
    app = manager.create_gateway_app()
    return TestClient(app)


def test_integration_server_discover_and_extensions(refactored_client: TestClient):
    auth_headers = {
        "X-API-Key": "admin-secret-key-123",
        "Mcp-Method": "server/discover",
    }
    res = refactored_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 100, "method": "server/discover", "params": {}},
        headers=auth_headers,
    )
    assert res.status_code == 200
    data = res.json()["result"]
    assert data["protocolVersion"] == "2026-07-28"
    assert data["capabilities"]["extensions"]["mcp-apps"]["supported"] is True
    assert data["capabilities"]["extensions"]["tasks"]["supported"] is True


def test_integration_tasks_stateful_lifecycle(refactored_client: TestClient):
    # 1. Get task (auto-created if not exists)
    res_get = refactored_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 101,
            "method": "tasks/get",
            "params": {"taskId": "integration-task-001"},
        },
        headers={"X-API-Key": "admin-secret-key-123", "Mcp-Method": "tasks/get"},
    )
    assert res_get.status_code == 200
    assert res_get.json()["result"]["taskId"] == "integration-task-001"

    # 2. Update task
    res_update = refactored_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 102,
            "method": "tasks/update",
            "params": {"taskId": "integration-task-001", "inputResponses": {"approved": True}},
        },
        headers={"X-API-Key": "admin-secret-key-123", "Mcp-Method": "tasks/update"},
    )
    assert res_update.status_code == 200
    assert res_update.json()["result"]["status"] == "completed"


def test_integration_header_enforcement_middleware(refactored_client: TestClient):
    # Header says tools/list, body says tools/call -> should trigger header validation middleware and return 400
    res = refactored_client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 103,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"message": "hello"}},
        },
        headers={"X-API-Key": "admin-secret-key-123", "Mcp-Method": "tools/list"},
    )
    assert res.status_code == 400
    assert "does not match" in res.json()["error"]["message"]
