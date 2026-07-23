"""
Tests for Enterprise Authentication Layer, Weak Protocol Rejection, and JWT.
"""

import jwt
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from mock_mcp_gateway.auth import AuthConfig, AuthMiddleware


def test_auth_config_parsing():
    raw_config = {
        "enabled": True,
        "header_name": "X-API-Key",
        "keys": [
            {"name": "admin", "key": "secret123", "allowed_servers": ["*"]},
            {"name": "system-user", "key": "secret456", "allowed_servers": ["system-server"]},
        ],
    }
    config = AuthConfig(raw_config)
    assert config.enabled is True
    assert "secret123" in config.keys
    assert config.keys["secret123"].can_access("any-server") is True
    assert config.keys["secret456"].can_access("system-server") is True
    assert config.keys["secret456"].can_access("utility-server") is False


def test_reject_weak_query_param_auth():
    raw_config = {"enabled": True, "header_name": "X-API-Key", "allow_anonymous_paths": ["/"]}
    auth_config = AuthConfig(raw_config)

    async def system_endpoint(request):
        return PlainTextResponse("System Endpoint")

    app = Starlette(routes=[Route("/system/sse", system_endpoint)])
    app.add_middleware(AuthMiddleware, auth_config=auth_config)
    client = TestClient(app)

    # Attempting query parameter authentication should be rejected with 400 Bad Request
    res = client.get("/system/sse?api_key=secret123")
    assert res.status_code == 400
    assert "Weak Protocol Disabled" in res.json()["error"]


def test_jwt_authentication():
    secret = "my-test-jwt-secret"
    raw_config = {
        "enabled": True,
        "type": "jwt",
        "jwt_secret": secret,
        "allow_anonymous_paths": ["/"],
    }
    auth_config = AuthConfig(raw_config)
    prefix_map = {"/system": "system-server"}

    async def system_endpoint(request):
        return PlainTextResponse("System Endpoint")

    app = Starlette(routes=[Route("/system/sse", system_endpoint)])
    app.add_middleware(AuthMiddleware, auth_config=auth_config, server_prefix_map=prefix_map)
    client = TestClient(app)

    # Valid JWT token
    token = jwt.encode(
        {"sub": "jwt-admin-client", "allowed_servers": ["*"]}, secret, algorithm="HS256"
    )

    res = client.get("/system/sse", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.text == "System Endpoint"

    # Invalid JWT signature
    bad_token = jwt.encode(
        {"sub": "hacker", "allowed_servers": ["*"]}, "wrong-secret", algorithm="HS256"
    )
    res = client.get("/system/sse", headers={"Authorization": f"Bearer {bad_token}"})
    assert res.status_code == 403


def test_auth_middleware_flow():
    raw_config = {
        "enabled": True,
        "header_name": "X-API-Key",
        "allow_anonymous_paths": ["/"],
        "keys": [
            {"name": "admin", "key": "admin-key", "allowed_servers": ["*"]},
            {"name": "sys-user", "key": "sys-key", "allowed_servers": ["system-server"]},
        ],
    }
    auth_config = AuthConfig(raw_config)
    prefix_map = {"/system": "system-server", "/utility": "utility-server"}

    async def home(request):
        return PlainTextResponse("Home")

    async def system_endpoint(request):
        return PlainTextResponse("System Endpoint")

    async def utility_endpoint(request):
        return PlainTextResponse("Utility Endpoint")

    app = Starlette(
        routes=[
            Route("/", home),
            Route("/system/sse", system_endpoint),
            Route("/utility/sse", utility_endpoint),
        ]
    )
    app.add_middleware(AuthMiddleware, auth_config=auth_config, server_prefix_map=prefix_map)

    client = TestClient(app)

    # 1. Anonymous path should succeed
    res = client.get("/")
    assert res.status_code == 200
    assert res.text == "Home"

    # 2. No API Key provided for protected route -> 401
    res = client.get("/system/sse")
    assert res.status_code == 401
    assert "Unauthorized" in res.json()["error"]

    # 3. Invalid API Key -> 403
    res = client.get("/system/sse", headers={"X-API-Key": "bad-key"})
    assert res.status_code == 403

    # 4. Valid Admin Key -> 200 on any server
    res = client.get("/system/sse", headers={"X-API-Key": "admin-key"})
    assert res.status_code == 200
    assert res.text == "System Endpoint"

    res = client.get("/utility/sse", headers={"Authorization": "Bearer admin-key"})
    assert res.status_code == 200
    assert res.text == "Utility Endpoint"

    # 5. Sys-user key on allowed server -> 200
    res = client.get("/system/sse", headers={"X-API-Key": "sys-key"})
    assert res.status_code == 200

    # 6. Sys-user key on disallowed server -> 403
    res = client.get("/utility/sse", headers={"X-API-Key": "sys-key"})
    assert res.status_code == 403
    assert "not authorized" in res.json()["message"]
