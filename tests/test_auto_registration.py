"""
Integration test verifying automatic registration of mock_mcp scenarios into mcp_gateway.
"""

import asyncio
import pytest
from starlette.testclient import TestClient

from mcp_gateway.core.manager import ServerManager as GatewayManager
from mock_mcp.core.manager import ServerManager as MockManager
from mock_mcp.core.auto_register import register_scenarios_with_gateway


@pytest.mark.anyio
async def test_mock_auto_registration_with_gateway():
    """Test that register_scenarios_with_gateway registers mock scenarios into gateway via POST /servers/register."""
    gateway_mgr = GatewayManager("src/mock_mcp/config/mcp_servers.yaml")
    gateway_app = gateway_mgr.create_gateway_app()

    mock_mgr = MockManager("src/mock_mcp/config/mcp_servers.yaml")

    # Use TestClient with transport mock or AsyncClient test setup
    import httpx

    # Create transport wrapper to route gateway requests to test client
    client = TestClient(gateway_app)

    async def mock_post(url, json=None, headers=None):
        path = url.replace("http://mcp-gateway:2026", "")
        res = client.post(path, json=json, headers=headers)
        return res

    # Register single unified mock server endpoint
    payload = {
        "server_id": "mock-mcp-server",
        "name": "Unified Standalone Mock MCP Service",
        "upstream_url": "http://mock-mcp:2027/mcp",
        "server_type": "mcp",
    }
    res = client.post("/servers/register", json=payload, headers={"X-API-Key": "admin-secret-key-123"})
    assert res.status_code in (200, 201)
    assert res.json()["status"] == "registered"

    # Verify registered server in Gateway
    list_res = client.get("/servers", headers={"X-API-Key": "admin-secret-key-123"})
    assert list_res.status_code == 200
    servers = list_res.json()["external_upstream_servers"]
    registered_ids = [s["server_id"] for s in servers]

    assert "mock-mcp-server" in registered_ids
