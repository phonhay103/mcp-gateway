"""
Tests for Scenario and Session Lifecycle API Endpoints:
- GET /scenarios
- POST /scenarios/select
- POST /scenarios/clear
- POST /sessions
- GET /sessions
- DELETE /sessions/{session_id}
- POST /sessions/close
"""

from pathlib import Path
import pytest
from starlette.testclient import TestClient

from mcp_gateway.manager import ServerManager


@pytest.fixture
def gateway_client():
    """Fixture providing a TestClient for a configured ServerManager Gateway app."""
    config_file = Path("config/mcp_servers.yaml")
    manager = ServerManager(config_file)
    app = manager.create_gateway_app()
    return TestClient(app)


def test_list_scenarios_endpoint(gateway_client: TestClient):
    res = gateway_client.get("/scenarios")
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "success"
    assert "count" in data
    assert "scenarios" in data
    assert len(data["scenarios"]) > 0

    scenario_ids = [s["id"] for s in data["scenarios"]]
    assert "sysops-scenario" in scenario_ids
    assert "utility-scenario" in scenario_ids


def test_select_scenario_endpoint(gateway_client: TestClient):
    payload = {
        "scenario_id": "sysops-scenario",
        "session_id": "my-test-session-999",
    }
    res = gateway_client.post("/scenarios/select", json=payload)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "selected"
    assert data["session_id"] == "my-test-session-999"
    assert data["active_scenario"]["id"] == "sysops-scenario"
    assert "get_system_info" in data["active_scenario"]["tools"]


def test_select_nonexistent_scenario(gateway_client: TestClient):
    payload = {"scenario_id": "non-existent-scenario"}
    res = gateway_client.post("/scenarios/select", json=payload)
    assert res.status_code == 404
    assert "not found" in res.json()["message"]


def test_clear_scenario_endpoint(gateway_client: TestClient):
    select_payload = {
        "scenario_id": "utility-scenario",
        "session_id": "session-to-clear",
    }
    gateway_client.post("/scenarios/select", json=select_payload)

    clear_payload = {
        "session_id": "session-to-clear",
        "scenario_id": "utility-scenario",
    }
    res = gateway_client.post("/scenarios/clear", json=clear_payload)
    assert res.status_code == 200
    data = res.json()

    assert data["status"] == "cleared"
    assert data["session_id"] == "session-to-clear"
    assert data["cleared_records"] >= 1


def test_session_lifecycle_endpoints(gateway_client: TestClient):
    # 1. Create a new Session explicitly
    create_payload = {
        "session_id": "explicit-session-101",
        "scenario_id": "sysops-scenario",
        "metadata": {"environment": "testing"},
    }
    res_create = gateway_client.post("/sessions", json=create_payload)
    assert res_create.status_code == 201
    data_create = res_create.json()
    assert data_create["status"] == "created"
    assert data_create["session_id"] == "explicit-session-101"
    assert data_create["scenario_id"] == "sysops-scenario"

    # 2. List Active Sessions
    res_list = gateway_client.get("/sessions")
    assert res_list.status_code == 200
    data_list = res_list.json()
    active_sids = [s["session_id"] for s in data_list["sessions"]]
    assert "explicit-session-101" in active_sids

    # 3. Close Session via DELETE /sessions/{session_id}
    res_close = gateway_client.delete("/sessions/explicit-session-101")
    assert res_close.status_code == 200
    data_close = res_close.json()
    assert data_close["status"] == "closed"
    assert data_close["session_id"] == "explicit-session-101"

    # Verify session is no longer active
    res_list_after = gateway_client.get("/sessions")
    active_sids_after = [s["session_id"] for s in res_list_after.json()["sessions"]]
    assert "explicit-session-101" not in active_sids_after


def test_close_session_via_post_endpoint(gateway_client: TestClient):
    # Create session
    gateway_client.post("/sessions", json={"session_id": "post-close-session"})

    # Close session via POST /sessions/close
    res_close = gateway_client.post("/sessions/close", json={"session_id": "post-close-session"})
    assert res_close.status_code == 200
    assert res_close.json()["status"] == "closed"
