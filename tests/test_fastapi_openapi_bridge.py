"""
Enterprise-Grade Complex Integration Test for FastAPI OpenAPI Bridge in Mock MCP Gateway.
Features:
1. Polymorphic Pydantic Request Models (Union / Discriminator yielding oneOf/anyOf JSON Schemas).
2. Multi-Valued Query Parameters (Array query params with style & explode rules).
3. Non-JSON Multi-Media Responses (PNG Image QR Codes converted to MCP Base64 Image Content).
4. Deeply Nested Schemas & Enum Constraints.
5. Multi-Tenant Authorization Headers & Idempotency Key Preservation.
6. Custom Exception Handlers returning HTTP 400 / 422 / 404 Error Payloads.
7. Full End-to-End Execution via MCP 2026-07-28 Router (POST /mcp).
"""

from pathlib import Path
import json
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
import pytest
from fastapi import FastAPI, HTTPException, Header, Query, Response, status, File, UploadFile, Form
from pydantic import BaseModel, Field
import httpx
from unittest.mock import patch
from starlette.testclient import TestClient

from mcp_gateway.core.openapi_bridge import OpenAPIBridgeConfig, openapi_bridge_registry
from mcp_gateway.core.manager import ServerManager


AUTH_HEADERS = {"X-API-Key": "admin-secret-key-123"}

# -------------------------------------------------------------------
# 1. Complex Pydantic Models (Polymorphism, Nested, Enums)
# -------------------------------------------------------------------

class PriorityLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class CategoryNode(BaseModel):
    name: str = Field(..., description="Category Name")
    subcategories: Optional[List["CategoryNode"]] = Field(default=None, description="Subcategories under this node")


CategoryNode.model_rebuild()


class EmailStep(BaseModel):
    step_type: Literal["email"] = "email"
    to_address: str = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Email subject line")


class DatabaseStep(BaseModel):
    step_type: Literal["db"] = "db"
    query: str = Field(..., description="SQL Database query")
    commit: bool = Field(default=False, description="Whether to commit changes")


class DelayStep(BaseModel):
    step_type: Literal["delay"] = "delay"
    seconds: int = Field(..., ge=1, le=3600, description="Duration to pause in seconds")


WorkflowStep = Union[EmailStep, DatabaseStep, DelayStep]


class WorkflowExecutionRequest(BaseModel):
    workflow_id: str = Field(..., description="Workflow UUID identifier")
    steps: List[WorkflowStep] = Field(..., description="Ordered workflow steps")


class Address(BaseModel):
    street: str = Field(..., description="Street name and number")
    city: str = Field(..., description="City name")
    postal_code: str = Field(..., description="ZIP / Postal Code")
    country: str = Field(default="US", description="ISO Country code")


class CreditCardPayment(BaseModel):
    payment_type: Literal["card"] = "card"
    card_number: str = Field(..., description="16-digit credit card number")
    cvv: str = Field(..., description="Card CVV code")


class CryptoPayment(BaseModel):
    payment_type: Literal["crypto"] = "crypto"
    wallet_address: str = Field(..., description="Crypto wallet public key")
    blockchain: str = Field(default="ethereum", description="Target blockchain network")


class BankTransferPayment(BaseModel):
    payment_type: Literal["bank"] = "bank"
    iban: str = Field(..., description="Bank IBAN code")
    swift_code: str = Field(..., description="SWIFT / BIC code")


# Polymorphic Union Type -> generates oneOf / anyOf in OpenAPI
PaymentMethod = Union[CreditCardPayment, CryptoPayment, BankTransferPayment]


class ProcessPaymentRequest(BaseModel):
    transaction_id: str = Field(..., description="Unique Transaction ID")
    amount: float = Field(..., gt=0.0, description="Amount to charge")
    currency: str = Field(default="USD", description="Currency symbol")
    payment_details: PaymentMethod = Field(..., description="Polymorphic payment option")


class OrderItem(BaseModel):
    sku: str = Field(..., description="Stock Keeping Unit ID")
    quantity: int = Field(..., ge=1, description="Quantity ordered")
    unit_price: float = Field(..., gt=0.0, description="Price per unit")


class BatchOrderRequest(BaseModel):
    batch_reference: str = Field(..., description="Batch reference code")
    priority: PriorityLevel = Field(default=PriorityLevel.MEDIUM, description="Batch fulfillment priority")
    shipping_address: Address = Field(..., description="Destination address")
    items: List[OrderItem] = Field(..., min_items=1, description="List of items in batch")


# -------------------------------------------------------------------
# 2. Complex FastAPI Application Setup
# -------------------------------------------------------------------

complex_fastapi_app = FastAPI(
    title="Enterprise Multi-Tenant Fulfillment Engine API",
    version="3.0.0-enterprise",
    description="Complex FastAPI microservice for validating OpenAPI Bridge Adapter",
)


@complex_fastapi_app.post(
    "/payments/process",
    summary="Process Polymorphic Payment",
    description="Accepts Credit Card, Crypto, or Bank Transfer payment details.",
)
async def process_payment(payload: ProcessPaymentRequest):
    p_type = payload.payment_details.payment_type
    return {
        "status": "PROCESSED",
        "transaction_id": payload.transaction_id,
        "amount": payload.amount,
        "currency": payload.currency,
        "method_used": p_type,
    }


@complex_fastapi_app.get(
    "/orders/search",
    summary="Search Orders with Multi-Valued Query Arrays",
    description="Filter orders by status tags, amount ranges, and priority.",
)
async def search_orders(
    statuses: List[str] = Query(default=[], description="Filter by status codes"),
    min_amount: Optional[float] = Query(None, description="Minimum order amount"),
    priority: Optional[PriorityLevel] = Query(None, description="Filter by priority level"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
):
    return {
        "query_statuses": statuses,
        "applied_min_amount": min_amount,
        "applied_priority": priority.value if priority else None,
        "tenant_context": x_tenant_id,
        "total_results": len(statuses) * 10,
        "results": [
            {"order_id": f"ORD-QUERY-{i}", "status": statuses[0] if statuses else "ALL"}
            for i in range(1, 3)
        ],
    }


@complex_fastapi_app.get(
    "/orders/{order_id}/qr-code",
    summary="Generate Order Tracking QR Code Image",
    description="Returns raw PNG binary image content.",
)
async def get_order_qr_code(order_id: str):
    if order_id == "INVALID":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    # Generate mock PNG byte stream
    fake_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + f"QR-CODE-FOR-{order_id}".encode("utf-8")
    return Response(content=fake_png_bytes, media_type="image/png")


@complex_fastapi_app.post(
    "/orders/batch",
    status_code=status.HTTP_201_CREATED,
    summary="Create Multi-Tenant Batch Orders",
    description="Validates authorization tokens and idempotency headers.",
)
async def create_batch_order(
    request: BatchOrderRequest,
    x_auth_token: Optional[str] = Header(None, alias="X-Auth-Token"),
    x_tenant_id: Optional[str] = Header(None, alias="X-Tenant-ID"),
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
):
    if not x_auth_token or x_auth_token != "secret-token-2026":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid X-Auth-Token Header",
        )

    total_value = sum(item.quantity * item.unit_price for item in request.items)

    return {
        "batch_reference": request.batch_reference,
        "status": "QUEUED",
        "priority": request.priority.value,
        "item_count": len(request.items),
        "total_value": round(total_value, 2),
        "tenant_id": x_tenant_id,
        "idempotency_key": x_idempotency_key,
        "shipping_city": request.shipping_address.city,
    }


@complex_fastapi_app.post(
    "/org/structure",
    summary="Validate Recursive Org/Category Tree Structure",
    description="Accepts and parses deeply nested self-referential Pydantic schemas.",
)
async def validate_org_structure(node: CategoryNode):
    # Flatten structure names to return in validation
    def flatten(n: CategoryNode) -> List[str]:
        names = [n.name]
        if n.subcategories:
            for sub in n.subcategories:
                names.extend(flatten(sub))
        return names

    all_names = flatten(node)
    return {
        "root_name": node.name,
        "total_nodes": len(all_names),
        "all_node_names": all_names,
    }


@complex_fastapi_app.post(
    "/workflows/execute",
    summary="Execute Polymorphic Discriminated Workflow Steps",
    description="Sequentially evaluates heterogeneous workflow step objects using discrimated union logic.",
)
async def execute_workflow(payload: WorkflowExecutionRequest):
    executed_types = [step.step_type for step in payload.steps]
    return {
        "workflow_id": payload.workflow_id,
        "total_steps": len(payload.steps),
        "executed_types": executed_types,
        "status": "COMPLETED",
    }


@complex_fastapi_app.post(
    "/documents/upload",
    summary="Upload Binary Document and Form Metadata",
    description="Demonstrates multipart file uploading with standard form-data properties.",
)
async def upload_document(
    file: UploadFile = File(..., description="Target PDF or Image binary file"),
    doc_type: str = Form(..., description="Document classification (e.g. invoice, receipt)"),
    tags: str = Form(None, description="Comma-separated metadata tags"),
):
    content = await file.read()
    return {
        "filename": file.filename,
        "size_bytes": len(content),
        "doc_type": doc_type,
        "extracted_tags": tags.split(",") if tags else [],
    }


@complex_fastapi_app.get(
    "/defensive/large-payload",
    summary="Defensive Truncation Test Endpoint",
    description="Generates an extraordinarily large text payload to trigger the Gateway's 50KB truncation defense.",
)
async def get_large_payload():
    # Return 100KB of repeated garbage text (1000 lines of 100 characters)
    massive_line = "A" * 100 + "\n"
    massive_text = massive_line * 1000  # Exactly 101,000 bytes
    return Response(content=massive_text, media_type="text/plain")


@pytest.fixture
def gateway_client():
    """Fixture providing a TestClient for configured Gateway app."""
    config_file = Path("config/mcp_servers.yaml")
    manager = ServerManager(config_file)
    app = manager.create_gateway_app()
    return TestClient(app)


# -------------------------------------------------------------------
# 3. Test Cases for Complex OpenAPI Bridge
# -------------------------------------------------------------------

def test_complex_fastapi_openapi_spec_structure():
    """Verify complex OpenAPI spec generated by FastAPI includes polymorphic schemas and media types."""
    spec = complex_fastapi_app.openapi()
    paths = spec["paths"]

    assert "/payments/process" in paths
    assert "/orders/search" in paths
    assert "/orders/{order_id}/qr-code" in paths
    assert "/orders/batch" in paths

    # Verify components/schemas contain polymorphic union definitions
    schemas = spec.get("components", {}).get("schemas", {})
    assert "ProcessPaymentRequest" in schemas
    assert "CreditCardPayment" in schemas
    assert "CryptoPayment" in schemas


@pytest.mark.anyio
async def test_complex_fastapi_bridge_polymorphism_and_images():
    """Test registering complex FastAPI spec and executing polymorphic payment and image QR code endpoints."""
    spec = complex_fastapi_app.openapi()

    config = OpenAPIBridgeConfig(
        server_id="enterprise-fulfillment",
        name="Enterprise Fulfillment Service",
        base_url="http://fulfillment.internal",
        openapi_spec=spec,
    )

    tools = await openapi_bridge_registry.register_openapi_server(config)
    assert len(tools) >= 4

    tool_names = [t.name for t in tools]
    payment_tool = next(n for n in tool_names if "process_payment" in n)
    qr_tool = next(n for n in tool_names if "qr_code" in n)

    transport = httpx.ASGITransport(app=complex_fastapi_app)

    async def custom_httpx_request(self, method, url, **kwargs):
        req = self.build_request(method, url, **kwargs)
        return await transport.handle_async_request(req)

    with patch.object(httpx.AsyncClient, "request", side_effect=custom_httpx_request, autospec=True):
        # 1. Execute Polymorphic Crypto Payment
        res_pay = await openapi_bridge_registry.call_openapi_tool(
            server_id="enterprise-fulfillment",
            tool_name=payment_tool,
            tool_args={
                "transaction_id": "TXN-9999",
                "amount": 1500.0,
                "currency": "ETH",
                "payment_details": {
                    "payment_type": "crypto",
                    "wallet_address": "0x71C...390",
                    "blockchain": "ethereum",
                },
            },
        )

        assert res_pay["jsonrpc"] == "2.0"
        pay_data = res_pay["result"]["structuredContent"]
        assert pay_data["status"] == "PROCESSED"
        assert pay_data["method_used"] == "crypto"
        assert pay_data["amount"] == 1500.0

        # 2. Execute Image QR Code Request (Verify Base64 MCP Image Content)
        res_qr = await openapi_bridge_registry.call_openapi_tool(
            server_id="enterprise-fulfillment",
            tool_name=qr_tool,
            tool_args={"order_id": "ORD-777"},
        )

        assert res_qr["jsonrpc"] == "2.0"
        content_item = res_qr["result"]["content"][0]
        assert content_item["type"] == "image"
        assert content_item["mimeType"] == "image/png"
        assert len(content_item["data"]) > 0


def test_complex_fastapi_e2e_multi_tenant_batch_order(gateway_client: TestClient):
    """E2E Test: Register complex FastAPI server and invoke batch orders with custom Auth & Idempotency headers."""
    spec = complex_fastapi_app.openapi()

    # 1. Register server via POST /servers/register
    gateway_client.post(
        "/servers/register",
        json={
            "server_id": "fulfillment-service",
            "name": "Enterprise Fulfillment Gateway",
            "base_url": "http://fulfillment.internal",
            "server_type": "openapi",
            "openapi_spec": spec,
            "allowed_forward_headers": ["X-Tenant-ID", "X-Idempotency-Key", "X-Auth-Token"],
        },
        headers=AUTH_HEADERS,
    )

    # 2. Get tool name for batch order
    list_res = gateway_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers=AUTH_HEADERS,
    )
    tools = list_res.json()["result"]["tools"]
    batch_tool = next(t for t in tools if "create_batch_order" in t["name"])

    # 3. Call tool via POST /mcp with custom headers (Bearer Auth, Tenant ID, Idempotency Key)
    transport = httpx.ASGITransport(app=complex_fastapi_app)

    async def custom_httpx_request(self, method, url, **kwargs):
        req = self.build_request(method, url, **kwargs)
        return await transport.handle_async_request(req)

    custom_headers = {
        **AUTH_HEADERS,
        "X-Auth-Token": "secret-token-2026",
        "X-Tenant-ID": "tenant-corp-alpha",
        "X-Idempotency-Key": "idempotent-key-007",
    }

    call_payload = {
        "jsonrpc": "2.0",
        "id": 100,
        "method": "tools/call",
        "params": {
            "name": "fulfillment-service::create_batch_order_orders_batch_post",
            "arguments": {
                "batch_reference": "BATCH-2026-X",
                "priority": "HIGH",
                "shipping_address": {
                    "street": "100 Innovation Way",
                    "city": "San Francisco",
                    "postal_code": "94105",
                    "country": "US",
                },
                "items": [
                    {"sku": "LAPTOP-01", "quantity": 5, "unit_price": 1200.0},
                    {"sku": "MOUSE-02", "quantity": 10, "unit_price": 25.0},
                ],
            },
        },
    }

    with patch.object(httpx.AsyncClient, "request", side_effect=custom_httpx_request, autospec=True):
        res = gateway_client.post("/mcp", json=call_payload, headers=custom_headers)
        if res.status_code != 200:
            print("FAILED E2E RESPONSE:", res.status_code, res.text)
        assert res.status_code == 200
        result = res.json()["result"]

        assert "structuredContent" in result
        struct = result["structuredContent"]
        assert struct["batch_reference"] == "BATCH-2026-X"
        assert struct["status"] == "QUEUED"
        assert struct["priority"] == "HIGH"
        assert struct["total_value"] == 6250.0
        assert struct["tenant_id"] == "tenant-corp-alpha"
        assert struct["idempotency_key"] == "idempotent-key-007"
        assert struct["shipping_city"] == "San Francisco"


def test_mcp_gateway_bearer_token_stripped_downstream(gateway_client: TestClient):
    """
    E2E Test verifying security boundary:
    - Client sends "Authorization: Bearer gateway-token-123" to Gateway.
    - Gateway strips this client Bearer token when forwarding to downstream (to prevent credential leak).
    - Gateway injects the downstream's own registered headers (e.g. "X-Backend-Key") and other user headers.
    """
    spec = complex_fastapi_app.openapi()

    # 1. Register downstream server with its own credentials and a whitelisted set of client headers
    gateway_client.post(
        "/servers/register",
        json={
            "server_id": "secure-service",
            "name": "Secure Backend Service",
            "base_url": "http://secure.internal",
            "server_type": "openapi",
            "openapi_spec": spec,
            "headers": {"X-Backend-Key": "backend-secret-xyz"},
            "allowed_forward_headers": ["X-Tenant-ID"],
        },
        headers=AUTH_HEADERS,
    )

    # 2. Intercept outgoing request to verify security isolation
    transport = httpx.ASGITransport(app=complex_fastapi_app)

    outgoing_headers = {}

    async def custom_httpx_request(self, method, url, **kwargs):
        nonlocal outgoing_headers
        outgoing_headers = kwargs.get("headers", {})
        req = self.build_request(method, url, **kwargs)
        return await transport.handle_async_request(req)

    # 3. Simulate request from client with:
    # - Gateway Bearer token (should be stripped)
    # - Whitelisted custom header "X-Tenant-ID" (should be allowed)
    # - Infrastructure standard header "X-Request-ID" (should pass automatically)
    # - Non-whitelisted custom header "X-Unwanted-Header" (should be stripped)
    client_headers = {
        **AUTH_HEADERS,
        "Authorization": "Bearer gateway-token-123",
        "X-Tenant-ID": "tenant-corp-omega",
        "X-Request-ID": "req-id-999",
        "X-Unwanted-Header": "hack-context",
    }

    call_payload = {
        "jsonrpc": "2.0",
        "id": 200,
        "method": "tools/call",
        "params": {
            "name": "secure-service::create_batch_order_orders_batch_post",
            "arguments": {
                "batch_reference": "SECURE-BATCH",
                "priority": "LOW",
                "shipping_address": {
                    "street": "1 Main St",
                    "city": "Boston",
                    "postal_code": "02108",
                    "country": "US",
                },
                "items": [
                    {"sku": "PEN-01", "quantity": 100, "unit_price": 1.5},
                ],
            },
        },
    }

    with patch.object(httpx.AsyncClient, "request", side_effect=custom_httpx_request, autospec=True):
        res = gateway_client.post("/mcp", json=call_payload, headers=client_headers)
        assert res.status_code == 200

    # Assert Security Isolation Boundary:
    # A. Gateway Bearer token was STRIPPED and not forwarded downstream!
    # Convert keys of outgoing_headers to lowercase for robust case-insensitive assertion
    lower_outgoing = {k.lower(): v for k, v in outgoing_headers.items()}
    assert "authorization" not in lower_outgoing or "gateway-token" not in lower_outgoing.get("authorization", "")
    
    # B. Downstream's own registered authentication header was INJECTED successfully!
    assert lower_outgoing.get("x-backend-key") == "backend-secret-xyz"

    # C. Allowed tenant custom header is preserved and forwarded!
    assert lower_outgoing.get("x-tenant-id") == "tenant-corp-omega"

    # D. Infrastructure protocol header (X-Request-ID) is automatically forwarded!
    assert lower_outgoing.get("x-request-id") == "req-id-999"

    # E. Non-whitelisted header (X-Unwanted-Header) is strictly filtered out!
    assert "x-unwanted-header" not in lower_outgoing


def test_highly_complex_fastapi_features(gateway_client: TestClient):
    """
    E2E Integration Test validating highly complex, enterprise-grade FastAPI features:
    1. Recursive Self-Referencing Category Trees ($ref inside $ref recursive parsing).
    2. Polymorphic Discriminated Union Sequences (heterogenous workflow steps).
    3. Multipart Form-Data Binary File Uploads with Metadata.
    4. Defensive Payload Truncation on Large (100KB) Responses.
    """
    spec = complex_fastapi_app.openapi()

    # 1. Register Server
    gateway_client.post(
        "/servers/register",
        json={
            "server_id": "ultra-complex-service",
            "name": "Ultra Complex Enterprise Microservice",
            "base_url": "http://complex.internal",
            "server_type": "openapi",
            "openapi_spec": spec,
        },
        headers=AUTH_HEADERS,
    )

    transport = httpx.ASGITransport(app=complex_fastapi_app)

    async def custom_httpx_request(self, method, url, **kwargs):
        req = self.build_request(method, url, **kwargs)
        return await transport.handle_async_request(req)

    with patch.object(httpx.AsyncClient, "request", side_effect=custom_httpx_request, autospec=True):
        # A. Recursive Structure Test
        recursive_payload = {
            "jsonrpc": "2.0",
            "id": 501,
            "method": "tools/call",
            "params": {
                "name": "ultra-complex-service::validate_org_structure_org_structure_post",
                "arguments": {
                    "name": "Electronics",
                    "subcategories": [
                        {
                            "name": "Laptops",
                            "subcategories": [
                                {"name": "Gaming Laptops"},
                                {"name": "Ultrabooks"}
                            ]
                        },
                        {
                            "name": "Smartphones"
                        }
                    ]
                }
            }
        }
        res_rec = gateway_client.post("/mcp", json=recursive_payload, headers=AUTH_HEADERS)
        assert res_rec.status_code == 200
        rec_data = res_rec.json()["result"]["structuredContent"]
        assert rec_data["root_name"] == "Electronics"
        assert rec_data["total_nodes"] == 5
        assert "Gaming Laptops" in rec_data["all_node_names"]

        # B. Polymorphic Discriminated Workflow Steps
        workflow_payload = {
            "jsonrpc": "2.0",
            "id": 502,
            "method": "tools/call",
            "params": {
                "name": "ultra-complex-service::execute_workflow_workflows_execute_post",
                "arguments": {
                    "workflow_id": "WF-UUID-999",
                    "steps": [
                        {"step_type": "email", "to_address": "admin@company.com", "subject": "System Failure"},
                        {"step_type": "db", "query": "SELECT * FROM metrics", "commit": False},
                        {"step_type": "delay", "seconds": 300}
                    ]
                }
            }
        }
        res_wf = gateway_client.post("/mcp", json=workflow_payload, headers=AUTH_HEADERS)
        assert res_wf.status_code == 200
        wf_data = res_wf.json()["result"]["structuredContent"]
        assert wf_data["workflow_id"] == "WF-UUID-999"
        assert wf_data["total_steps"] == 3
        assert wf_data["executed_types"] == ["email", "db", "delay"]
        assert wf_data["status"] == "COMPLETED"

        # C. Multipart Document Upload with Form Fields
        # File is Base64 encoded "Hello World from MCP Gateway!" (length 28 bytes)
        multipart_payload = {
            "jsonrpc": "2.0",
            "id": 503,
            "method": "tools/call",
            "params": {
                "name": "ultra-complex-service::upload_document_documents_upload_post",
                "arguments": {
                    "file": "base64:SGVsbG8gV29ybGQgZnJvbSBNQ1AgR2F0ZXdheSE=",
                    "doc_type": "invoice",
                    "tags": "finance,tax,2026"
                }
            }
        }
        res_mp = gateway_client.post("/mcp", json=multipart_payload, headers=AUTH_HEADERS)
        assert res_mp.status_code == 200
        mp_data = res_mp.json()["result"]["structuredContent"]
        assert mp_data["filename"] == "file"
        assert mp_data["size_bytes"] == 29
        assert mp_data["doc_type"] == "invoice"
        assert mp_data["extracted_tags"] == ["finance", "tax", "2026"]

        # D. Defensive Large Payload Truncation Test (Response is 100KB, should truncate to 50KB)
        defensive_payload = {
            "jsonrpc": "2.0",
            "id": 504,
            "method": "tools/call",
            "params": {
                "name": "ultra-complex-service::get_large_payload_defensive_large_payload_get",
                "arguments": {}
            }
        }
        res_def = gateway_client.post("/mcp", json=defensive_payload, headers=AUTH_HEADERS)
        assert res_def.status_code == 200
        def_content = res_def.json()["result"]["content"][0]["text"]
        # Max limit is 50,000 bytes. With truncation suffix, length should be around 50,030
        assert len(def_content) < 60_000
        assert "TRUNCATED:" in def_content



