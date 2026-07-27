"""
Unit and Integration Tests for OpenAPI REST Server Bridge Adapter in Mock MCP Gateway.
Verifies spec parsing, schema translation, Control Plane registration, Data Plane routing, and REST proxying.
"""

from pathlib import Path
import json
from typing import Any, Dict
import pytest
from unittest.mock import AsyncMock, patch
import httpx
from starlette.testclient import TestClient

from mcp_gateway.core.adapters import AdapterStatus, AdapterType, adapter_registry
from mcp_gateway.core.openapi_bridge import OpenAPIBridgeConfig, OpenAPIParser, openapi_bridge_registry
from mcp_gateway.core.schemas import ToolDefinition, schema_registry
from mcp_gateway.core.manager import ServerManager


AUTH_HEADERS = {"X-API-Key": "admin-secret-key-123"}

# Sample OpenAPI Spec
SAMPLE_OPENAPI_SPEC = {
    "openapi": "3.0.0",
    "info": {
        "title": "Sample User & Pet Management API",
        "version": "1.0.0",
        "description": "API for testing OpenAPI Bridge Adapter",
    },
    "paths": {
        "/users/{id}": {
            "get": {
                "operationId": "getUserById",
                "summary": "Get user by ID",
                "description": "Retrieve user details using user ID.",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "description": "User ID",
                        "schema": {"type": "integer"},
                    },
                    {
                        "name": "include_orders",
                        "in": "query",
                        "required": False,
                        "description": "Whether to include order history",
                        "schema": {"type": "boolean"},
                    },
                ],
                "responses": {"200": {"description": "User details"}},
            }
        },
        "/pets": {
            "post": {
                "operationId": "create_pet",
                "summary": "Create a new pet",
                "description": "Add a pet to the database.",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string", "description": "Pet name"},
                                    "species": {"type": "string", "description": "Pet species"},
                                },
                                "required": ["name", "species"],
                            }
                        }
                    },
                },
                "responses": {"201": {"description": "Created"}},
            }
        },
    },
}


@pytest.fixture
def gateway_client():
    """Fixture providing a TestClient for a configured ServerManager Gateway app."""
    config_file = Path("config/mcp_servers.yaml")
    manager = ServerManager(config_file)
    app = manager.create_gateway_app()
    return TestClient(app)


def test_openapi_parser_spec():
    """Test parsing OpenAPI spec parameters and request body into MCP Tool inputSchema."""
    operations = OpenAPIParser.parse_spec(SAMPLE_OPENAPI_SPEC, server_id="test-api")
    assert len(operations) == 2

    # Check getUserById operation
    get_op = next(op for op in operations if op.tool_name == "getUserById")
    assert get_op.method == "GET"
    assert get_op.path == "/users/{id}"
    assert "id" in get_op.input_schema["properties"]
    assert "include_orders" in get_op.input_schema["properties"]
    assert "id" in get_op.input_schema["required"]

    # Check create_pet operation
    post_op = next(op for op in operations if op.tool_name == "create_pet")
    assert post_op.method == "POST"
    assert post_op.path == "/pets"
    assert "name" in post_op.input_schema["properties"]
    assert "species" in post_op.input_schema["properties"]
    assert "name" in post_op.input_schema["required"]


@pytest.mark.anyio
async def test_register_openapi_server():
    """Test registering an OpenAPI server bridge and verifying Control Plane status."""
    config = OpenAPIBridgeConfig(
        server_id="petstore-openapi",
        name="Petstore OpenAPI Bridge",
        base_url="http://mock-petstore.internal/v1",
        openapi_spec=SAMPLE_OPENAPI_SPEC,
    )

    tools = await openapi_bridge_registry.register_openapi_server(config)
    assert len(tools) == 2

    tool_names = [t.name for t in tools]
    assert "getUserById" in tool_names
    assert "create_pet" in tool_names

    # Verify registration in Adapter Registry
    adapter = adapter_registry.get_adapter("petstore-openapi")
    assert adapter is not None
    assert adapter.adapter_type == AdapterType.OPENAPI_BRIDGE
    assert adapter.status == AdapterStatus.RUNNING
    assert "getUserById" in adapter.tools

    # Verify registration in Schema Registry
    schema_tool = schema_registry.get_tool("getUserById")
    assert schema_tool is not None
    assert schema_tool.name == "getUserById"


def test_servers_register_api_endpoint(gateway_client: TestClient):
    """Test registering an OpenAPI server via Control Plane REST API POST /servers/register."""
    payload = {
        "server_id": "api-gateway-openapi",
        "name": "External REST API Bridge",
        "base_url": "http://api.external.com/v1",
        "server_type": "openapi",
        "openapi_spec": SAMPLE_OPENAPI_SPEC,
    }

    resp = gateway_client.post("/servers/register", json=payload, headers=AUTH_HEADERS)
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "registered"
    assert data["server_type"] == "openapi"
    assert data["discovered_tools_count"] == 2

    # Verify server appears in GET /servers
    list_resp = gateway_client.get("/servers", headers=AUTH_HEADERS)
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    assert "openapi_bridge_servers" in list_data
    bridge_ids = [s["server_id"] for s in list_data["openapi_bridge_servers"]]
    assert "api-gateway-openapi" in bridge_ids


def test_mcp_router_tools_list_with_openapi_bridge(gateway_client: TestClient):
    """Test that POST /mcp tools/list includes tools discovered from OpenAPI Bridge."""
    payload = {
        "server_id": "finance-openapi",
        "name": "Finance REST Server",
        "base_url": "http://finance.internal",
        "server_type": "openapi",
        "openapi_spec": SAMPLE_OPENAPI_SPEC,
    }
    gateway_client.post("/servers/register", json=payload, headers=AUTH_HEADERS)

    # Query POST /mcp tools/list
    jsonrpc_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }
    res = gateway_client.post("/mcp", json=jsonrpc_req, headers=AUTH_HEADERS)
    assert res.status_code == 200
    data = res.json()
    tools = data["result"]["tools"]

    tool_names = [t["name"] for t in tools]
    assert "getUserById" in tool_names or "finance-openapi::getUserById" in tool_names


@pytest.mark.anyio
async def test_openapi_tool_execution():
    """Test executing a tool call through OpenAPI Bridge using mock httpx.AsyncClient."""
    config = OpenAPIBridgeConfig(
        server_id="mock-user-service",
        name="Mock User Service",
        base_url="http://user-service.internal",
        openapi_spec=SAMPLE_OPENAPI_SPEC,
    )
    await openapi_bridge_registry.register_openapi_server(config)

    req = httpx.Request("GET", "http://user-service.internal/users/42?include_orders=true")
    mock_resp = httpx.Response(
        200,
        request=req,
        json={"id": 42, "name": "Alice", "role": "admin"},
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_resp

        result = await openapi_bridge_registry.call_openapi_tool(
            server_id="mock-user-service",
            tool_name="getUserById",
            tool_args={"id": 42, "include_orders": True},
        )

        assert result["jsonrpc"] == "2.0"
        content = result["result"]["content"][0]["text"]
        assert "Alice" in content


def test_custom_header_preservation_end_to_end(gateway_client: TestClient):
    """Test that custom HTTP headers sent by the client are preserved and forwarded end-to-end to the OpenAPI REST server."""
    # 1. Register OpenAPI Server Bridge
    payload = {
        "server_id": "tenant-service-openapi",
        "name": "Tenant REST API",
        "base_url": "http://tenant-service.internal",
        "server_type": "openapi",
        "openapi_spec": SAMPLE_OPENAPI_SPEC,
        "allowed_forward_headers": ["X-Custom-Tenant-ID", "X-Correlation-ID", "X-User-Role"],
    }
    gateway_client.post("/servers/register", json=payload, headers=AUTH_HEADERS)

    # 2. Invoke tool via POST /mcp with custom headers
    jsonrpc_req = {
        "jsonrpc": "2.0",
        "id": 999,
        "method": "tools/call",
        "params": {
            "name": "tenant-service-openapi::getUserById",
            "arguments": {"id": 100, "include_orders": False},
        },
    }

    custom_client_headers = {
        **AUTH_HEADERS,
        "X-Custom-Tenant-ID": "tenant-xyz-999",
        "X-Correlation-ID": "corr-uuid-777",
        "X-User-Role": "supervisor",
    }

    req = httpx.Request("GET", "http://tenant-service.internal/users/100")
    mock_resp = httpx.Response(200, request=req, json={"id": 100, "name": "Bob", "tenant": "tenant-xyz-999"})

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_resp

        res = gateway_client.post("/mcp", json=jsonrpc_req, headers=custom_client_headers)
        assert res.status_code == 200

        # Verify that mock_request was called and that custom headers were preserved and forwarded
        assert mock_request.called
        call_kwargs = mock_request.call_args.kwargs
        outbound_headers = call_kwargs.get("headers", {})

        # Lowercase comparisons or key checks
        headers_lower = {k.lower(): v for k, v in outbound_headers.items()}
        assert headers_lower.get("x-custom-tenant-id") == "tenant-xyz-999"
        assert headers_lower.get("x-correlation-id") == "corr-uuid-777"
        assert headers_lower.get("x-user-role") == "supervisor"


def test_ref_dereferencing_and_schema_normalization():
    """Test recursive $ref dereferencing and OpenAPI 3.0 nullable/allOf schema normalization."""
    spec_with_refs = {
        "openapi": "3.0.0",
        "components": {
            "schemas": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string", "nullable": True},
                        "city": {"type": "string"},
                    },
                },
                "UserWithAddress": {
                    "allOf": [
                        {
                            "type": "object",
                            "properties": {"id": {"type": "integer"}},
                        },
                        {
                            "type": "object",
                            "properties": {
                                "address": {"$ref": "#/components/schemas/Address"}
                            },
                        },
                    ]
                },
            }
        },
        "paths": {
            "/users": {
                "post": {
                    "operationId": "create_user_with_address",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/UserWithAddress"}
                            }
                        }
                    },
                }
            }
        },
    }

    operations = OpenAPIParser.parse_spec(spec_with_refs, server_id="ref-api")
    assert len(operations) == 1
    op = operations[0]

    # Verify allOf merged properties
    props = op.input_schema["properties"]
    assert "id" in props
    assert "address" in props

    # Verify nested $ref resolved and nullable: True converted to type: ['string', 'null']
    addr_props = props["address"]["properties"]
    assert "street" in addr_props
    assert addr_props["street"]["type"] == ["string", "null"]


@pytest.mark.anyio
async def test_image_and_truncated_response_handling():
    """Test image base64 encoding and large text payload truncation defense."""
    config = OpenAPIBridgeConfig(
        server_id="media-api",
        name="Media API",
        base_url="http://media.internal",
        openapi_spec=SAMPLE_OPENAPI_SPEC,
    )
    await openapi_bridge_registry.register_openapi_server(config)

    # 1. Test image response handling
    req = httpx.Request("GET", "http://media.internal/image.png")
    fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    mock_img_resp = httpx.Response(
        200, request=req, content=fake_png, headers={"content-type": "image/png"}
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_img_resp
        result = await openapi_bridge_registry.call_openapi_tool(
            server_id="media-api", tool_name="getUserById", tool_args={"id": 1}
        )

        content = result["result"]["content"][0]
        assert content["type"] == "image"
        assert content["mimeType"] == "image/png"
        assert len(content["data"]) > 0

    # 2. Test payload truncation defense
    huge_json_str = json.dumps({"data": "X" * 100_000})
    mock_huge_resp = httpx.Response(
        200, request=req, text=huge_json_str, headers={"content-type": "application/json"}
    )

    with patch.object(httpx.AsyncClient, "request", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = mock_huge_resp
        result = await openapi_bridge_registry.call_openapi_tool(
            server_id="media-api", tool_name="getUserById", tool_args={"id": 1}
        )

        content_text = result["result"]["content"][0]["text"]
        assert "[TRUNCATED:" in content_text
        assert len(content_text) < 60_000


def test_mcp_2026_07_28_schema_compliance():
    """Test that OpenAPIParser constructs tool definitions fully compliant with MCP 2026-07-28 spec (outputSchema, annotations, title)."""
    spec_2026 = {
        "openapi": "3.0.0",
        "paths": {
            "/products/{id}": {
                "get": {
                    "operationId": "get_product",
                    "summary": "Get product details",
                    "description": "Retrieve product by ID",
                    "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {
                        "200": {
                            "description": "Product details",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "integer"},
                                            "title": {"type": "string"},
                                            "price": {"type": "number"},
                                        },
                                        "required": ["id", "title", "price"],
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }

    operations = OpenAPIParser.parse_spec(spec_2026, server_id="store-api")
    assert len(operations) == 1
    op = operations[0]

    tool_def = ToolDefinition(
        name=op.tool_name,
        title=op.summary or op.tool_name,
        description=op.description,
        inputSchema=op.input_schema,
        outputSchema=op.output_schema,
        annotations=op.annotations,
    )

    # Verify MCP 2026-07-28 fields
    mcp_dump = tool_def.to_mcp_format()
    assert mcp_dump["name"] == "get_product"
    assert mcp_dump["title"] == "Get product details"
    assert "outputSchema" in mcp_dump
    assert mcp_dump["outputSchema"]["properties"]["price"]["type"] == "number"
    assert mcp_dump["annotations"]["readOnlyHint"] is True
    assert mcp_dump["annotations"]["destructiveHint"] is False


def test_mcp_2026_json_schema_draft202012_combinators():
    """Test normalization of anyOf, oneOf, allOf, array items, and $defs per JSON Schema Draft 2020-12."""
    poly_spec = {
        "openapi": "3.1.0",
        "paths": {
            "/notify": {
                "post": {
                    "operationId": "send_notification",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "oneOf": [
                                        {
                                            "type": "object",
                                            "properties": {
                                                "email": {"type": "string"},
                                                "subject": {"type": "string", "nullable": True},
                                            },
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "phone": {"type": "string"},
                                                "sms_body": {"type": "string"},
                                            },
                                        },
                                    ]
                                }
                            }
                        }
                    },
                }
            }
        },
    }

    operations = OpenAPIParser.parse_spec(poly_spec, server_id="poly-api")
    assert len(operations) == 1
    op = operations[0]

    # Verify polymorphic requestBody fields gathered into inputSchema properties
    props = op.input_schema["properties"]
    assert "email" in props
    assert "phone" in props
    assert "subject" in props
    assert "sms_body" in props

    # Verify Draft 2020-12 nullable conversion
    assert props["subject"]["type"] == ["string", "null"]




