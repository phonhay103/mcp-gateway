"""Tests for Mock MCP Server Tools."""

from mcp_gateway.tools import (
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
    assert err_res.is_error is True
    assert "division by zero" in str(err_res.content)


def test_manage_users():
    # List
    users = manage_users("list")
    assert "users" in users

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


from unittest.mock import patch, MagicMock

def test_http_ping():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        res = http_ping("https://httpbin.org/get", timeout_seconds=2.0)
        assert isinstance(res, dict)
        assert res["url"] == "https://httpbin.org/get"
        assert res["success"] is True
