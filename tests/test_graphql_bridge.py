"""
Unit and Integration Tests for GraphQL Server Bridge Adapter in Mock MCP Gateway.
Verifies schema parsing (SDL), tool translation, Control Plane registration, Data Plane routing, and GraphQL proxying.
"""

from pathlib import Path
import json
from typing import Any, Dict
import pytest
from unittest.mock import AsyncMock, patch
import httpx
from starlette.testclient import TestClient

from mcp_gateway.core.adapters import AdapterStatus, AdapterType, adapter_registry
from mcp_gateway.core.graphql_bridge import GraphQLBridgeConfig, GraphQLParser, graphql_bridge_registry
from mcp_gateway.core.schemas import ToolDefinition, schema_registry
from mcp_gateway.core.manager import ServerManager


AUTH_HEADERS = {"X-API-Key": "admin-secret-key-123"}

SAMPLE_GRAPHQL_SDL = """
type User {
    id: ID!
    name: String!
    email: String
    age: Int
}

type Query {
    user(id: ID!): User
    users(limit: Int): [User!]!
}

type Mutation {
    createUser(name: String!, email: String!, age: Int): User!
    deleteUser(id: ID!): Boolean!
}
"""


@pytest.fixture
def gateway_client():
    config_file = Path("src/mock_mcp/config/mcp_servers.yaml")
    manager = ServerManager(config_file)
    app = manager.create_gateway_app()
    return TestClient(app)


def test_graphql_parser_sdl():
    from graphql import build_schema
    schema = build_schema(SAMPLE_GRAPHQL_SDL)
    operations = GraphQLParser.parse_schema(schema, server_id="test-gql")
    assert len(operations) == 4

    tool_names = {op.tool_name for op in operations}
    assert "query_user" in tool_names
    assert "query_users" in tool_names
    assert "mutation_createUser" in tool_names
    assert "mutation_deleteUser" in tool_names

    create_op = next(op for op in operations if op.tool_name == "mutation_createUser")
    assert create_op.operation_type == "mutation"
    assert "name" in create_op.input_schema["properties"]
    assert "email" in create_op.input_schema["properties"]
    assert "name" in create_op.input_schema["required"]


@pytest.mark.anyio
async def test_graphql_bridge_registration_and_call():
    config = GraphQLBridgeConfig(
        server_id="test-gql-server",
        name="Test GraphQL Server",
        base_url="http://localhost:4000/graphql",
        graphql_schema=SAMPLE_GRAPHQL_SDL,
    )

    tools = await graphql_bridge_registry.register_graphql_server(config)
    assert len(tools) == 4

    adapter = adapter_registry.get_adapter("test-gql-server")
    assert adapter is not None
    assert adapter.adapter_type == AdapterType.GRAPHQL_BRIDGE
    assert adapter.status == AdapterStatus.RUNNING

    mock_resp = httpx.Response(
        200,
        json={"data": {"user": {"id": "1", "name": "Alice", "email": "alice@example.com", "age": 30}}},
        request=httpx.Request("POST", "http://localhost:4000/graphql"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp

        result = await graphql_bridge_registry.call_graphql_tool(
            server_id="test-gql-server",
            tool_name="query_user",
            tool_args={"id": "1"},
        )

        assert "result" in result
        assert "content" in result["result"]
        assert "structuredContent" in result["result"]
        assert result["result"]["structuredContent"]["user"]["name"] == "Alice"

    graphql_bridge_registry.unregister_server("test-gql-server")


def test_gateway_graphql_server_registration_endpoint(gateway_client: TestClient):
    payload = {
        "server_id": "api-gateway-gql",
        "name": "Gateway GraphQL API",
        "base_url": "http://localhost:4000/graphql",
        "server_type": "graphql",
        "graphql_schema": SAMPLE_GRAPHQL_SDL,
    }

    resp = gateway_client.post("/servers/register", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "registered"
    assert data["server_type"] == "graphql"
    assert data["discovered_tools_count"] == 4

    list_resp = gateway_client.get("/servers", headers=AUTH_HEADERS)
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert "graphql_bridge_servers" in list_data
    gql_ids = [s["server_id"] for s in list_data["graphql_bridge_servers"]]
    assert "api-gateway-gql" in gql_ids

    del_resp = gateway_client.delete("/servers/api-gateway-gql", headers=AUTH_HEADERS)
    assert del_resp.status_code == 200
