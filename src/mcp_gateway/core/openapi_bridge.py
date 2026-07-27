"""
OpenAPI REST Server Bridge Registry & Dynamic Translation Engine for Mock MCP Gateway.
Parses OpenAPI v3.0 / v3.1 Specifications dynamically and translates REST endpoints into MCP Tools.
Proxies MCP 'tools/call' requests to REST endpoints over HTTP while enforcing Gateway
Security (Hash-Pinning, Description Sanitization, Authorization), trace recording, and metrics.
Optimized for:
1. $ref Dereferencing (Recursive Schema Resolution)
2. Schema Dialect Normalization (OpenAPI 3.0 vs 3.1, nullable, allOf merging)
3. Smart Query Parameter Serialization (style & explode rules)
4. Multi-Media Response Handling (Binary/Image encoding) and Large Payload Truncation Defense.
"""

import re
import json
import base64
from typing import Any, Dict, List, Optional, Set
import httpx
from pydantic import BaseModel, Field

from mcp_gateway.logger import logger
from mcp_gateway.observability.metrics import metrics_collector
from mcp_gateway.core.schemas import ToolDefinition, schema_registry
from mcp_gateway.security import ToolDescriptionSanitizer, hash_pinning_guard, filter_forwardable_headers
from mcp_gateway.core.adapters import AdapterRegistry, AdapterStatus, AdapterType, PlugInAdapterConfig, adapter_registry


MAX_RESPONSE_BYTES = 50_000  # 50KB safety limit for LLM context window protection


class OpenAPIBridgeConfig(BaseModel):
    """Pydantic v2 Model for OpenAPI REST Server Bridge Registration."""

    server_id: str = Field(..., description="Unique OpenAPI Server ID / Adapter Name")
    name: str = Field(..., description="Human readable adapter name")
    base_url: str = Field(..., description="Base URL of REST API server (e.g. 'http://api.example.com/v1')")
    openapi_spec_url: Optional[str] = Field(default=None, description="URL to fetch openapi.json/yaml spec")
    openapi_spec_path: Optional[str] = Field(default=None, description="Local file path to openapi.json/yaml spec")
    openapi_spec: Optional[Dict[str, Any]] = Field(default=None, description="Inline OpenAPI JSON/YAML spec dictionary")
    headers: Dict[str, str] = Field(default_factory=dict, description="Custom HTTP headers for REST calls (e.g. Authorization)")
    allowed_forward_headers: List[str] = Field(default_factory=list, description="Whitelist of client-supplied headers allowed to be forwarded downstream")
    timeout_seconds: float = Field(default=30.0, ge=1.0, description="HTTP timeout in seconds")
    scenario_id: Optional[str] = Field(default=None, description="Optional Scenario ID to associate with")

    model_config = {"extra": "ignore"}


class OpenAPIOperation(BaseModel):
    """Internal model representing a parsed OpenAPI operation translated to an MCP Tool."""

    tool_name: str
    method: str
    path: str
    summary: str = ""
    description: str = ""
    parameters: List[Dict[str, Any]] = Field(default_factory=list)
    request_body_schema: Optional[Dict[str, Any]] = None
    media_type: str = "application/json"
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    annotations: Optional[Dict[str, Any]] = None

    model_config = {"extra": "ignore"}


class OpenAPIParser:
    """Parser converting OpenAPI 3.0 / 3.1 Specification into MCP Tool Definitions."""

    @staticmethod
    def sanitize_tool_name(raw_name: str) -> str:
        """Sanitize raw name into a valid identifier name."""
        cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", raw_name)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned or "unnamed_tool"

    @classmethod
    def resolve_refs(cls, node: Any, root_spec: Dict[str, Any], seen: Optional[Set[str]] = None) -> Any:
        """Recursively resolve JSON Schema $ref pointers within the OpenAPI specification."""
        if seen is None:
            seen = set()

        if isinstance(node, dict):
            if "$ref" in node and isinstance(node["$ref"], str):
                ref_path = node["$ref"]
                if ref_path in seen:
                    # Circular reference defense
                    return {"type": "object", "description": f"[Circular Ref: {ref_path}]"}

                seen.add(ref_path)

                # Resolve pointer like #/components/schemas/User
                if ref_path.startswith("#/"):
                    parts = ref_path.lstrip("#/").split("/")
                    curr = root_spec
                    for part in parts:
                        if isinstance(curr, dict) and part in curr:
                            curr = curr[part]
                        else:
                            curr = None
                            break
                    if isinstance(curr, dict):
                        resolved = cls.resolve_refs(curr, root_spec, set(seen))
                        merged = dict(resolved) if isinstance(resolved, dict) else {}
                        for k, v in node.items():
                            if k != "$ref":
                                merged[k] = v
                        return merged

            # Recursive for dict key/values
            new_dict = {}
            for k, v in node.items():
                new_dict[k] = cls.resolve_refs(v, root_spec, set(seen))
            return new_dict

        elif isinstance(node, list):
            return [cls.resolve_refs(item, root_spec, set(seen)) for item in node]

        return node

    @classmethod
    def normalize_schema(cls, schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize OpenAPI 3.0/3.1 schema to JSON Schema Draft 2020-12 (MCP 2026-07-28 compliant).
        Supports:
        - type arrays e.g. type: ["string", "null"] (converting OpenAPI 3.0 nullable: true)
        - native $ref and $defs pointers
        - recursive allOf, anyOf, oneOf, not combinators
        - items, prefixItems, properties, and additionalProperties
        """
        if not isinstance(schema, dict):
            return schema or {}

        s = dict(schema)

        # 1. Handle OpenAPI 3.0 nullable: true -> Draft 2020-12 type array or anyOf
        if s.get("nullable") is True:
            s.pop("nullable", None)
            curr_type = s.get("type")
            if isinstance(curr_type, str):
                s["type"] = [curr_type, "null"]
            elif isinstance(curr_type, list) and "null" not in curr_type:
                s["type"] = curr_type + ["null"]
            elif not curr_type:
                if "anyOf" in s and isinstance(s["anyOf"], list):
                    s["anyOf"].append({"type": "null"})
                elif "oneOf" in s and isinstance(s["oneOf"], list):
                    s["anyOf"] = s.pop("oneOf") + [{"type": "null"}]
                else:
                    s["anyOf"] = [{"type": "string"}, {"type": "null"}]

        # 2. Normalize recursive combinators: allOf, anyOf, oneOf, not
        for combinator in ("allOf", "anyOf", "oneOf"):
            if combinator in s and isinstance(s[combinator], list):
                s[combinator] = [cls.normalize_schema(item) for item in s[combinator]]

        if "not" in s and isinstance(s["not"], dict):
            s["not"] = cls.normalize_schema(s["not"])

        # 3. Handle allOf property flattening if allOf contains simple object schemas
        if "allOf" in s and isinstance(s["allOf"], list):
            all_objects = all(isinstance(sub, dict) and (sub.get("type") in ("object", None) or "properties" in sub) for sub in s["allOf"])
            if all_objects:
                merged_props: Dict[str, Any] = dict(s.get("properties", {}))
                merged_req: List[str] = list(s.get("required", []))
                for sub_s in s["allOf"]:
                    merged_props.update(sub_s.get("properties", {}))
                    for req_item in sub_s.get("required", []):
                        if req_item not in merged_req:
                            merged_req.append(req_item)
                s["type"] = "object"
                s["properties"] = merged_props
                if merged_req:
                    s["required"] = merged_req
                s.pop("allOf", None)

        # 4. Recursively normalize nested properties & additionalProperties
        if "properties" in s and isinstance(s["properties"], dict):
            s["properties"] = {
                k: cls.normalize_schema(v) for k, v in s["properties"].items()
            }

        if "additionalProperties" in s and isinstance(s["additionalProperties"], dict):
            s["additionalProperties"] = cls.normalize_schema(s["additionalProperties"])

        # 5. Normalize array items & prefixItems
        if "items" in s:
            if isinstance(s["items"], dict):
                s["items"] = cls.normalize_schema(s["items"])
            elif isinstance(s["items"], list):
                s["items"] = [cls.normalize_schema(item) for item in s["items"]]

        if "prefixItems" in s and isinstance(s["prefixItems"], list):
            s["prefixItems"] = [cls.normalize_schema(item) for item in s["prefixItems"]]

        # 6. Normalize $defs
        if "$defs" in s and isinstance(s["$defs"], dict):
            s["$defs"] = {k: cls.normalize_schema(v) for k, v in s["$defs"].items()}

        return s

    @classmethod
    def parse_spec(cls, spec: Dict[str, Any], server_id: str) -> List[OpenAPIOperation]:
        """Parse OpenAPI specification dictionary into a list of OpenAPIOperation objects."""
        # 1. Dereference all $ref pointers across spec
        resolved_spec = cls.resolve_refs(spec, root_spec=spec)

        operations: List[OpenAPIOperation] = []
        paths = resolved_spec.get("paths", {})

        for path_str, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            # Common path parameters
            common_params = path_item.get("parameters", [])

            for method in ["get", "post", "put", "delete", "patch", "head", "options"]:
                if method not in path_item:
                    continue

                op_data = path_item[method]
                if not isinstance(op_data, dict):
                    continue

                # Derive tool name
                op_id = op_data.get("operationId")
                if op_id:
                    tool_name = cls.sanitize_tool_name(op_id)
                else:
                    clean_path = path_str.replace("{", "").replace("}", "")
                    tool_name = cls.sanitize_tool_name(f"{method}_{clean_path}")

                # Combine summary and description
                summary = op_data.get("summary", "")
                desc = op_data.get("description", "")
                combined_desc = summary
                if desc:
                    combined_desc = f"{summary}\n{desc}" if summary else desc
                if not combined_desc:
                    combined_desc = f"OpenAPI REST Endpoint [{method.upper()} {path_str}]"

                sanitized_desc = ToolDescriptionSanitizer.sanitize_description(combined_desc)

                # Merge parameters
                op_params = common_params + op_data.get("parameters", [])

                # Parse Request Body schema (application/json, multipart/form-data)
                req_body = op_data.get("requestBody", {})
                req_body_schema = None
                media_type = "application/json"
                if isinstance(req_body, dict):
                    content = req_body.get("content", {})
                    if "application/json" in content:
                        media_type = "application/json"
                        req_body_schema = content["application/json"].get("schema")
                    elif "multipart/form-data" in content:
                        media_type = "multipart/form-data"
                        req_body_schema = content["multipart/form-data"].get("schema")
                    elif "*/*" in content:
                        media_type = "*/*"
                        req_body_schema = content["*/*"].get("schema")

                # Build unified MCP inputSchema with normalization
                input_schema = cls._build_input_schema(op_params, req_body_schema)

                # Parse 200/201 response schema for MCP 2026-07-28 outputSchema
                responses = op_data.get("responses", {})
                resp_200 = responses.get("200") or responses.get("201") or responses.get("2XX") or responses.get("default") or {}
                output_schema = None
                if isinstance(resp_200, dict):
                    resp_content = resp_200.get("content", {})
                    json_resp = resp_content.get("application/json", {}) or resp_content.get("*/*", {})
                    raw_out_schema = json_resp.get("schema")
                    if raw_out_schema:
                        output_schema = cls.normalize_schema(raw_out_schema)

                method_upper = method.upper()
                annotations = {
                    "readOnlyHint": method_upper in ("GET", "HEAD", "OPTIONS"),
                    "destructiveHint": method_upper == "DELETE",
                    "openApiEndpoint": f"{method_upper} {path_str}",
                }

                op_obj = OpenAPIOperation(
                    tool_name=tool_name,
                    method=method_upper,
                    path=path_str,
                    summary=summary,
                    description=sanitized_desc,
                    parameters=op_params,
                    request_body_schema=req_body_schema,
                    media_type=media_type,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    annotations=annotations,
                )
                operations.append(op_obj)

        return operations

    @classmethod
    def _build_input_schema(
        cls, parameters: List[Dict[str, Any]], request_body_schema: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Combine query, path, header params and requestBody schema into a single MCP inputSchema."""
        properties: Dict[str, Any] = {}
        required: List[str] = []

        # 1. Add OpenAPI parameters (path, query, header)
        for param in parameters:
            if not isinstance(param, dict):
                continue
            p_name = param.get("name")
            if not p_name:
                continue

            p_in = param.get("in", "query")
            p_schema = param.get("schema", {"type": "string"})
            p_desc = param.get("description", "")

            param_prop = cls.normalize_schema(p_schema)
            if p_desc:
                param_prop["description"] = f"[{p_in}] {p_desc}"

            properties[p_name] = param_prop
            if (param.get("required") or p_in == "path") and p_in != "header":
                if p_name not in required:
                    required.append(p_name)

        # 2. Merge JSON Request Body schema if present
        if request_body_schema and isinstance(request_body_schema, dict):
            normalized_body = cls.normalize_schema(request_body_schema)

            body_props = dict(normalized_body.get("properties", {}))
            body_req = list(normalized_body.get("required", []))

            # Handle polymorphic request bodies (anyOf / oneOf / allOf)
            for comb in ("anyOf", "oneOf", "allOf"):
                if comb in normalized_body and isinstance(normalized_body[comb], list):
                    for branch in normalized_body[comb]:
                        if isinstance(branch, dict):
                            branch_props = branch.get("properties", {})
                            for bp_name, bp_schema in branch_props.items():
                                if bp_name not in body_props:
                                    body_props[bp_name] = bp_schema

            for b_name, b_schema in body_props.items():
                if b_name not in properties:
                    properties[b_name] = b_schema
                if b_name in body_req and b_name not in required:
                    required.append(b_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }


class OpenAPIBridgeRegistry:
    """Registry managing active OpenAPI REST Server Bridge instances and tool routing."""

    def __init__(self):
        self._servers: Dict[str, OpenAPIBridgeConfig] = {}
        self._operations: Dict[str, Dict[str, OpenAPIOperation]] = {}  # server_id -> {tool_name -> OpenAPIOperation}
        self._tool_to_server: Dict[str, str] = {}  # tool_name -> server_id

    def list_servers(self) -> List[Dict[str, Any]]:
        """List all registered OpenAPI bridge servers."""
        return [srv.model_dump() for srv in self._servers.values()]

    def get_server(self, server_id: str) -> Optional[OpenAPIBridgeConfig]:
        """Get config for registered OpenAPI bridge server."""
        return self._servers.get(server_id)

    def unregister_server(self, server_id: str) -> bool:
        """Unregister an OpenAPI bridge server and remove its tools."""
        if server_id in self._servers:
            self._servers.pop(server_id)
            ops = self._operations.pop(server_id, {})
            for t_name in ops.keys():
                self._tool_to_server.pop(t_name, None)

            adapter_registry.unregister_adapter(server_id)
            logger.info(f"Unregistered OpenAPI Bridge Server [{server_id}]")
            return True
        return False

    async def register_openapi_server(
        self, config: OpenAPIBridgeConfig, headers_override: Optional[Dict[str, str]] = None
    ) -> List[ToolDefinition]:
        """
        Register an OpenAPI REST Server bridge.
        Fetches or loads spec, parses endpoints into MCP tools, applies Hash-Pinning,
        and registers with Control Plane AdapterRegistry and SchemaRegistry.
        """
        spec = config.openapi_spec

        # 1. Fetch or read OpenAPI spec if not provided inline
        if not spec:
            if config.openapi_spec_url:
                filtered_override = filter_forwardable_headers(headers_override or {}, config.allowed_forward_headers)
                fetch_headers = {**config.headers}
                fetch_headers.update(filtered_override)
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    resp = await client.get(config.openapi_spec_url, headers=fetch_headers)
                    resp.raise_for_status()
                    spec = resp.json()
            elif config.openapi_spec_path:
                with open(config.openapi_spec_path, "r", encoding="utf-8") as f:
                    spec = json.load(f)
            else:
                raise ValueError("Must provide openapi_spec, openapi_spec_url, or openapi_spec_path")

        if not isinstance(spec, dict):
            raise ValueError("OpenAPI Spec must be a valid JSON dictionary")

        self._servers[config.server_id] = config
        operations = OpenAPIParser.parse_spec(spec, config.server_id)

        verified_tools: List[ToolDefinition] = []
        op_map: Dict[str, OpenAPIOperation] = {}
        tool_names: List[str] = []

        for op in operations:
            tool_def = ToolDefinition(
                name=op.tool_name,
                title=op.summary or op.tool_name,
                description=op.description,
                inputSchema=op.input_schema,
                outputSchema=op.output_schema,
                annotations=op.annotations,
            )

            # Security: Schema Hash Pinning Verification
            hash_ok = hash_pinning_guard.verify_tool_schema(op.tool_name, tool_def.to_mcp_format())
            if not hash_ok:
                logger.error(f"OpenAPI Tool '{op.tool_name}' from [{config.server_id}] failed SHA-256 Hash-Pinning check!")
                continue

            verified_tools.append(tool_def)
            op_map[op.tool_name] = op
            self._tool_to_server[op.tool_name] = config.server_id
            tool_names.append(op.tool_name)

            schema_registry.register_tool_definition(tool_def)

        self._operations[config.server_id] = op_map

        # Register as Plug-In Adapter in Control Plane
        adapter_cfg = PlugInAdapterConfig(
            name=config.server_id,
            description=config.name or f"OpenAPI REST Bridge for {config.base_url}",
            adapter_type=AdapterType.OPENAPI_BRIDGE,
            base_url=config.base_url,
            openapi_spec_url=config.openapi_spec_url,
            openapi_spec_path=config.openapi_spec_path,
            headers=config.headers,
            allowed_forward_headers=config.allowed_forward_headers,
            tools=tool_names,
            status=AdapterStatus.RUNNING,
        )
        adapter_registry.register_adapter(adapter_cfg)

        logger.info(f"Registered OpenAPI Bridge [{config.server_id}] with {len(verified_tools)} REST API tools")
        return verified_tools

    async def call_openapi_tool(
        self,
        server_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        headers_override: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an MCP 'tools/call' by translating it into a REST HTTP call (GET/POST/PUT/DELETE)
        to the target OpenAPI server endpoint.
        """
        srv = self.get_server(server_id)
        if not srv:
            return {
                "jsonrpc": "2.0",
                "id": "openapi-error",
                "error": {"code": -32002, "message": f"OpenAPI Bridge Server '{server_id}' not found in registry"},
            }

        op_map = self._operations.get(server_id, {})
        op = op_map.get(tool_name)
        if not op:
            return {
                "jsonrpc": "2.0",
                "id": "openapi-error",
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found on OpenAPI Bridge '{server_id}'"},
            }

        tool_args = tool_args or {}

        # 1. Separate path, query, header, and body arguments
        target_path = op.path
        query_params: Dict[str, Any] = {}

        # Filter client headers based on whitelist and protocol standards
        filtered_override = filter_forwardable_headers(headers_override or {}, srv.allowed_forward_headers)

        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            **srv.headers,
        }
        headers.update(filtered_override)

        body_data: Dict[str, Any] = {}
        consumed_args = set()

        for param in op.parameters:
            p_name = param.get("name")
            p_in = param.get("in", "query")

            if p_name in tool_args:
                val = tool_args[p_name]
                consumed_args.add(p_name)

                if p_in == "path":
                    target_path = target_path.replace(f"{{{p_name}}}", str(val))
                elif p_in == "query":
                    # Handle query array style & explode rules
                    p_style = param.get("style", "form")
                    p_explode = param.get("explode", p_style == "form")
                    if isinstance(val, list) and not p_explode:
                        query_params[p_name] = ",".join(str(x) for x in val)
                    else:
                        query_params[p_name] = val
                elif p_in == "header":
                    headers[p_name] = str(val)

        # Remaining args belong to requestBody
        for arg_k, arg_v in tool_args.items():
            if arg_k not in consumed_args:
                body_data[arg_k] = arg_v

        full_url = f"{srv.base_url.rstrip('/')}/{target_path.lstrip('/')}"

        async with httpx.AsyncClient(timeout=srv.timeout_seconds) as client:
            try:
                if op.method in ["POST", "PUT", "PATCH"]:
                    if op.media_type == "multipart/form-data":
                        files_payload = {}
                        data_payload = {}
                        for k, v in body_data.items():
                            # If value looks like a base64 encoded file
                            if isinstance(v, str) and (v.startswith("data:") or v.startswith("base64:")):
                                try:
                                    if v.startswith("data:"):
                                        _, base64_data = v.split(",", 1)
                                        raw_bytes = base64.b64decode(base64_data)
                                    else:
                                        raw_bytes = base64.b64decode(v[7:])
                                    files_payload[k] = (k, raw_bytes)
                                except Exception:
                                    files_payload[k] = (k, v.encode("utf-8"))
                            elif isinstance(v, (dict, list)):
                                data_payload[k] = json.dumps(v)
                            else:
                                data_payload[k] = str(v)

                        resp = await client.request(
                            method=op.method,
                            url=full_url,
                            params=query_params,
                            # Allow httpx to calculate content-type with boundary automatically
                            headers={k: v for k, v in headers.items() if k.lower() != "content-type"},
                            data=data_payload if data_payload else None,
                            files=files_payload if files_payload else None,
                        )
                    else:
                        resp = await client.request(
                            method=op.method,
                            url=full_url,
                            params=query_params,
                            headers=headers,
                            json=body_data if body_data else None,
                        )
                else:
                    resp = await client.request(
                        method=op.method,
                        url=full_url,
                        params=query_params,
                        headers=headers,
                    )

                await resp.aread()

                metrics_collector.record_request(op.method, full_url, resp.status_code)

                # Process response content (Images/Binary vs Text/JSON with truncation defense)
                content_type = resp.headers.get("content-type", "").lower()
                res_json = None

                if "image/" in content_type:
                    encoded_img = base64.b64encode(resp.content).decode("ascii")
                    mcp_content = [
                        {
                            "type": "image",
                            "data": encoded_img,
                            "mimeType": content_type.split(";")[0].strip(),
                        }
                    ]
                else:
                    try:
                        res_json = resp.json()
                        res_text = json.dumps(res_json, ensure_ascii=False, indent=2)
                    except Exception:
                        res_text = resp.text

                    # Truncation defense against huge payloads overwhelming LLM context window
                    if len(res_text) > MAX_RESPONSE_BYTES:
                        truncated_chars = len(res_text) - MAX_RESPONSE_BYTES
                        res_text = (
                            res_text[:MAX_RESPONSE_BYTES]
                            + f"\n\n... [TRUNCATED: Response payload reduced by {truncated_chars} chars to protect LLM context window limit]"
                        )

                    mcp_content = [{"type": "text", "text": res_text}]

                result_payload = {"content": mcp_content}
                if res_json is not None and isinstance(res_json, (dict, list)):
                    result_payload["structuredContent"] = res_json

                if resp.is_error:
                    result_payload["isError"] = True
                    return {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "result": result_payload,
                    }

                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": result_payload,
                }

            except httpx.TimeoutException:
                logger.error(f"Timeout executing OpenAPI tool '{tool_name}' on REST endpoint [{op.method} {full_url}]")
                return {
                    "jsonrpc": "2.0",
                    "id": "openapi-timeout",
                    "error": {"code": -32004, "message": f"OpenAPI REST Server '{server_id}' timed out for tool '{tool_name}'"},
                }
            except Exception as exc:
                logger.error(f"Error executing OpenAPI tool '{tool_name}' on [{full_url}]: {exc}")
                return {
                    "jsonrpc": "2.0",
                    "id": "openapi-error",
                    "error": {"code": -32002, "message": f"OpenAPI Bridge proxy error: {exc}"},
                }


# Global singleton instance
openapi_bridge_registry = OpenAPIBridgeRegistry()
