"""Tests for Mock MCP Server Tools."""

from mock_mcp_gateway.tools import (
    calculate,
    echo,
    generate_random_data,
    get_system_info,
    http_ping,
    manage_users,
)


def test_echo():
    res = echo("hello mcp")
    assert res["original_message"] == "hello mcp"
    assert res["length"] == 9
    assert res["reversed"] == "pcm olleh"
    assert res["uppercase"] == "HELLO MCP"


def test_get_system_info():
    info = get_system_info()
    assert "os" in info
    assert "python_version" in info
    assert "uptime_seconds" in info


def test_calculate():
    res = calculate("2 + 3 * 4")
    assert res["status"] == "success"
    assert res["result"] == 14

    err_res = calculate("1 / 0")
    assert err_res["status"] == "error"


def test_manage_users():
    # List
    users = manage_users("list")
    assert "users" in users
    initial_count = users["count"]

    # Create
    created = manage_users("create", name="Test User", email="test@example.com")
    assert created["status"] == "created"
    new_id = created["user"]["id"]

    # Get
    fetched = manage_users("get", user_id=new_id)
    assert fetched["user"]["name"] == "Test User"

    # Delete
    deleted = manage_users("delete", user_id=new_id)
    assert deleted["status"] == "deleted"


def test_generate_random_data():
    res = generate_random_data(type_name="uuid", count=3)
    assert res["count"] == 3
    assert len(res["items"]) == 3


def test_http_ping():
    res = http_ping("https://httpbin.org/get", timeout_seconds=2.0)
    # Ping should return a structured result dict regardless of connectivity
    assert "url" in res
    assert "success" in res
