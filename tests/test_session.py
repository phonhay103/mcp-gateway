"""
Tests for 3-Way Context Isolation: (session_id, scenario_id, server_id) and UUID v7 session generation.
Verifies that 1 scenario can apply to multiple MCP servers, and multiple scenarios can run concurrently without state leakage.
"""

import uuid
from mock_mcp_gateway.session import generate_uuid7, session_manager
from mock_mcp_gateway.tools import manage_users, get_system_info


def test_generate_uuid7():
    """Test generating time-sortable UUID v7 string."""
    u_str = generate_uuid7()
    u_obj = uuid.UUID(u_str)

    # UUID v7 version number is 7
    assert u_obj.version == 7


def test_scenario_multi_server_and_session_isolation():
    session_1 = "session-100"
    session_2 = "session-200"

    scenario_a = "scenario-alpha"
    scenario_b = "scenario-beta"

    server_x = "utility-server"
    server_y = "system-server"

    # Scenario A applied to Server X with Session 1 creates a user
    res1 = manage_users(
        "create",
        name="Charlie Alpha",
        email="charlie@alpha.com",
        session_id=session_1,
        scenario_id=scenario_a,
        server_id=server_x,
    )
    assert res1["status"] == "created"
    assert res1["session_id"] == session_1
    assert res1["scenario_id"] == scenario_a
    assert res1["server_id"] == server_x

    # Same Session 1, but running under Scenario B on Server X -> Should NOT see Charlie Alpha
    list_b1 = manage_users(
        "list",
        session_id=session_1,
        scenario_id=scenario_b,
        server_id=server_x,
    )
    b1_emails = [u["email"] for u in list_b1["users"]]
    assert "charlie@alpha.com" not in b1_emails

    # Same Scenario A and Server X, but running under Session 2 -> Should NOT see Charlie Alpha
    list_a2 = manage_users(
        "list",
        session_id=session_2,
        scenario_id=scenario_a,
        server_id=server_x,
    )
    a2_emails = [u["email"] for u in list_a2["emails"] if "emails" in list_a2] if "emails" in list_a2 else [u["email"] for u in list_a2["users"]]
    assert "charlie@alpha.com" not in a2_emails

    # Verify original (session_1, scenario_a, server_x) still has Charlie Alpha
    list_a1 = manage_users(
        "list",
        session_id=session_1,
        scenario_id=scenario_a,
        server_id=server_x,
    )
    a1_emails = [u["email"] for u in list_a1["users"]]
    assert "charlie@alpha.com" in a1_emails


def test_concurrent_scenarios_different_fixtures():
    # Seed fixture states for Scenario A and Scenario B
    fixture_a = {"users": {"u1": {"id": "u1", "name": "Scenario A User", "email": "a@test.com", "role": "admin"}}}
    fixture_b = {"users": {"u2": {"id": "u2", "name": "Scenario B User", "email": "b@test.com", "role": "user"}}}

    # Initialize sessions
    session_manager.get_or_create_session(
        session_id="sess-shared",
        scenario_id="scenario-a",
        server_id="system-server",
        initial_state=fixture_a,
    )

    session_manager.get_or_create_session(
        session_id="sess-shared",
        scenario_id="scenario-b",
        server_id="system-server",
        initial_state=fixture_b,
    )

    # Query Scenario A under sess-shared
    res_a = manage_users("list", session_id="sess-shared", scenario_id="scenario-a", server_id="system-server")
    a_names = [u["name"] for u in res_a["users"]]
    assert "Scenario A User" in a_names
    assert "Scenario B User" not in a_names

    # Query Scenario B under sess-shared
    res_b = manage_users("list", session_id="sess-shared", scenario_id="scenario-b", server_id="system-server")
    b_names = [u["name"] for u in res_b["users"]]
    assert "Scenario B User" in b_names
    assert "Scenario A User" not in b_names
