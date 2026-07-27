"""
GraphQL Server Bridge Registry & Dynamic Translation Engine for Mock MCP Gateway.
Parses GraphQL Schemas (SDL or Introspection) dynamically and translates Queries and Mutations into MCP Tools.
Proxies MCP 'tools/call' requests to GraphQL endpoints over HTTP while enforcing Gateway
Security (Hash-Pinning, Description Sanitization, Authorization), trace recording, and metrics.
"""

import re
import json
import base64
from typing import Any, Dict, List, Optional, Set
import httpx
from pydantic import BaseModel, Field

from graphql import (
    build_schema,
    build_client_schema,
    parse,
    GraphQLSchema,
    GraphQLObjectType,
    GraphQLNonNull,
    GraphQLList,
    GraphQLEnumType,
    GraphQLScalarType,
    GraphQLInputObjectType,
)

from mcp_gateway.observability import logger, metrics_collector
from mcp_gateway.core.schemas import ToolDefinition, schema_registry
from mcp_gateway.security.security import ToolDescriptionSanitizer, hash_pinning_guard, filter_forwardable_headers
from mcp_gateway.core.adapters import AdapterRegistry, AdapterStatus, AdapterType, PlugInAdapterConfig, adapter_registry


MAX_RESPONSE_BYTES = 50_000  # 50KB safety limit for LLM context window protection

INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      ...FullType
    }
    directives {
      name
      description
      locations
      args {
        ...InputValue
      }
    }
  }
}

fragment FullType on __Type {
  kind
  name
  description
  fields(includeDeprecated: true) {
    name
    description
    args {
      ...InputValue
    }
    type {
      ...TypeRef
    }
    isDeprecated
    deprecationReason
  }
  inputFields {
    ...InputValue
  }
  interfaces {
    ...TypeRef
  }
  enumValues(includeDeprecated: true) {
    name
    description
    isDeprecated
    deprecationReason
  }
  possibleTypes {
    ...TypeRef
  }
}

fragment InputValue on __InputValue {
  name
  description
  type {
    ...TypeRef
  }
  defaultValue
}

fragment TypeRef on __Type {
  kind
  name
  ofType {
    kind
    name
    ofType {
      kind
      name
      ofType {
        kind
        name
        ofType {
          kind
          name
          ofType {
            kind
            name
            ofType {
              kind
              name
            }
          }
        }
      }
    }
  }
}
"""


class GraphQLBridgeConfig(BaseModel):
    """Pydantic v2 Model for GraphQL Server Bridge Registration."""

    server_id: str = Field(..., description="Unique GraphQL Server ID / Adapter Name")
    name: str = Field(..., description="Human readable adapter name")
    base_url: str = Field(..., description="Base URL of GraphQL API endpoint (e.g. 'http://api.example.com/graphql')")
    graphql_schema_url: Optional[str] = Field(default=None, description="URL to fetch GraphQL schema SDL or introspection JSON")
    graphql_schema_path: Optional[str] = Field(default=None, description="Local file path to GraphQL schema SDL or introspection JSON")
    graphql_schema: Optional[Any] = Field(default=None, description="Inline GraphQL schema SDL string or dictionary")
    headers: Dict[str, str] = Field(default_factory=dict, description="Custom HTTP headers for GraphQL calls (e.g. Authorization)")
    allowed_forward_headers: List[str] = Field(default_factory=list, description="Whitelist of client-supplied headers allowed to be forwarded downstream")
    timeout_seconds: float = Field(default=30.0, ge=1.0, description="HTTP timeout in seconds")

    model_config = {"extra": "ignore"}


class GraphQLOperation(BaseModel):
    """Internal model representing a parsed GraphQL operation (Query or Mutation) translated to an MCP Tool."""

    tool_name: str
    operation_type: str  # "query" or "mutation"
    field_name: str
    description: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    output_schema: Optional[Dict[str, Any]] = None
    annotations: Optional[Dict[str, Any]] = None

    model_config = {"extra": "ignore"}


class GraphQLParser:
    """Parser converting GraphQL Schema into MCP Tool Definitions."""

    @staticmethod
    def sanitize_tool_name(raw_name: str) -> str:
        """Sanitize raw name into a valid identifier name."""
        cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", raw_name)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_")
        return cleaned or "unnamed_tool"

    @classmethod
    def _convert_type_to_json_schema(cls, gql_type: Any, defs: Optional[Set[str]] = None) -> Dict[str, Any]:
        """Convert a GraphQL type object to a JSON Schema Draft 2020-12 dictionary."""
        if defs is None:
            defs = set()

        if isinstance(gql_type, GraphQLNonNull):
            inner = cls._convert_type_to_json_schema(gql_type.of_type, defs)
            return inner

        if isinstance(gql_type, GraphQLList):
            inner = cls._convert_type_to_json_schema(gql_type.of_type, defs)
            return {"type": "array", "items": inner}

        if isinstance(gql_type, GraphQLScalarType):
            t_name = gql_type.name.lower()
            if t_name in ("int", "integer"):
                return {"type": "integer"}
            elif t_name in ("float", "number"):
                return {"type": "number"}
            elif t_name in ("boolean", "bool"):
                return {"type": "boolean"}
            elif t_name == "id":
                return {"type": ["string", "integer"]}
            else:
                return {"type": "string"}

        if isinstance(gql_type, GraphQLEnumType):
            values = [v.name for v in gql_type.values.values()]
            return {"type": "string", "enum": values}

        if isinstance(gql_type, (GraphQLInputObjectType, GraphQLObjectType)):
            properties = {}
            required = []
            if hasattr(gql_type, "fields"):
                fields = gql_type.fields
                for f_name, f_field in fields.items():
                    is_required = isinstance(f_field.type, GraphQLNonNull)
                    prop_schema = cls._convert_type_to_json_schema(f_field.type, defs)
                    if hasattr(f_field, "description") and f_field.description:
                        prop_schema["description"] = f_field.description
                    properties[f_name] = prop_schema
                    if is_required:
                        required.append(f_name)
            res = {"type": "object", "properties": properties}
            if required:
                res["required"] = required
            return res

        return {"type": "string"}

    @classmethod
    def parse_schema(cls, schema: GraphQLSchema, server_id: str) -> List[GraphQLOperation]:
        """Parse a compiled GraphQLSchema into a list of GraphQLOperation objects."""
        operations: List[GraphQLOperation] = []

        query_type = schema.query_type
        mutation_type = schema.mutation_type

        # 1. Parse Queries
        if query_type and hasattr(query_type, "fields"):
            for field_name, field_def in query_type.fields.items():
                tool_name = cls.sanitize_tool_name(f"query_{field_name}")
                desc = field_def.description or f"GraphQL Query: {field_name}"
                sanitized_desc = ToolDescriptionSanitizer.sanitize_description(desc)

                input_schema = cls._build_field_input_schema(field_def)
                output_schema = cls._convert_type_to_json_schema(field_def.type)

                annotations = {
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "graphqlEndpoint": field_name,
                    "graphqlOperationType": "query",
                }

                op = GraphQLOperation(
                    tool_name=tool_name,
                    operation_type="query",
                    field_name=field_name,
                    description=sanitized_desc,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    annotations=annotations,
                )
                operations.append(op)

        # 2. Parse Mutations
        if mutation_type and hasattr(mutation_type, "fields"):
            for field_name, field_def in mutation_type.fields.items():
                tool_name = cls.sanitize_tool_name(f"mutation_{field_name}")
                desc = field_def.description or f"GraphQL Mutation: {field_name}"
                sanitized_desc = ToolDescriptionSanitizer.sanitize_description(desc)

                input_schema = cls._build_field_input_schema(field_def)
                output_schema = cls._convert_type_to_json_schema(field_def.type)

                annotations = {
                    "readOnlyHint": False,
                    "destructiveHint": "delete" in field_name.lower() or "remove" in field_name.lower(),
                    "graphqlEndpoint": field_name,
                    "graphqlOperationType": "mutation",
                }

                op = GraphQLOperation(
                    tool_name=tool_name,
                    operation_type="mutation",
                    field_name=field_name,
                    description=sanitized_desc,
                    input_schema=input_schema,
                    output_schema=output_schema,
                    annotations=annotations,
                )
                operations.append(op)

        return operations

    @classmethod
    def _build_field_input_schema(cls, field_def: Any) -> Dict[str, Any]:
        """Build MCP inputSchema for a GraphQL field's arguments."""
        properties: Dict[str, Any] = {}
        required: List[str] = []

        if hasattr(field_def, "args") and field_def.args:
            for arg_name, arg_def in field_def.args.items():
                is_required = isinstance(arg_def.type, GraphQLNonNull)
                prop_schema = cls._convert_type_to_json_schema(arg_def.type)
                if hasattr(arg_def, "description") and arg_def.description:
                    prop_schema["description"] = arg_def.description
                properties[arg_name] = prop_schema
                if is_required:
                    required.append(arg_name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    @staticmethod
    def generate_selection_set(gql_type: Any, depth: int = 2) -> str:
        """Recursively generate a GraphQL selection set string for a given GraphQL return type."""
        if depth <= 0:
            return ""

        unwrapped = gql_type
        while hasattr(unwrapped, "of_type"):
            unwrapped = unwrapped.of_type

        if isinstance(unwrapped, (GraphQLObjectType, GraphQLInputObjectType)) and hasattr(unwrapped, "fields"):
            fields = unwrapped.fields
            sel_fields = []
            for f_name, f_field in fields.items():
                f_type = f_field.type
                base_type = f_type
                while hasattr(base_type, "of_type"):
                    base_type = base_type.of_type

                if isinstance(base_type, (GraphQLScalarType, GraphQLEnumType)):
                    sel_fields.append(f_name)
                elif isinstance(base_type, GraphQLObjectType) and depth > 1:
                    sub_sel = GraphQLParser.generate_selection_set(base_type, depth - 1)
                    if sub_sel:
                        sel_fields.append(f"{f_name} {sub_sel}")
                    else:
                        sel_fields.append(f_name)
                else:
                    sel_fields.append(f_name)
            if sel_fields:
                return "{ " + " ".join(sel_fields) + " }"
        return ""


class GraphQLBridgeRegistry:
    """Registry managing active GraphQL Server Bridge instances and tool routing."""

    def __init__(self):
        self._servers: Dict[str, GraphQLBridgeConfig] = {}
        self._operations: Dict[str, Dict[str, GraphQLOperation]] = {}  # server_id -> {tool_name -> GraphQLOperation}
        self._tool_to_server: Dict[str, str] = {}  # tool_name -> server_id
        self._schemas: Dict[str, GraphQLSchema] = {}  # server_id -> GraphQLSchema

    def list_servers(self) -> List[Dict[str, Any]]:
        """List all registered GraphQL bridge servers."""
        return [srv.model_dump() for srv in self._servers.values()]

    def get_server(self, server_id: str) -> Optional[GraphQLBridgeConfig]:
        """Get config for registered GraphQL bridge server."""
        return self._servers.get(server_id)

    def unregister_server(self, server_id: str) -> bool:
        """Unregister a GraphQL bridge server and remove its tools."""
        if server_id in self._servers:
            self._servers.pop(server_id)
            ops = self._operations.pop(server_id, {})
            for t_name in ops.keys():
                self._tool_to_server.pop(t_name, None)
            self._schemas.pop(server_id, None)

            adapter_registry.unregister_adapter(server_id)
            logger.info(f"Unregistered GraphQL Bridge Server [{server_id}]")
            return True
        return False

    async def register_graphql_server(
        self, config: GraphQLBridgeConfig, headers_override: Optional[Dict[str, str]] = None
    ) -> List[ToolDefinition]:
        """
        Register a GraphQL Server bridge.
        Fetches or loads schema (SDL or Introspection), parses fields into MCP tools,
        applies Hash-Pinning, and registers with Control Plane AdapterRegistry and SchemaRegistry.
        """
        schema = None
        raw_schema = config.graphql_schema

        if not raw_schema:
            if config.graphql_schema_url:
                filtered_override = filter_forwardable_headers(headers_override or {}, config.allowed_forward_headers)
                fetch_headers = {**config.headers, **filtered_override}
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    resp = await client.get(config.graphql_schema_url, headers=fetch_headers)
                    resp.raise_for_status()
                    content = resp.text
                    try:
                        data = json.loads(content)
                        if "data" in data and "__schema" in data["data"]:
                            raw_schema = data["data"]
                        else:
                            raw_schema = content
                    except Exception:
                        raw_schema = content
            elif config.graphql_schema_path:
                with open(config.graphql_schema_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    try:
                        data = json.loads(content)
                        if "data" in data and "__schema" in data["data"]:
                            raw_schema = data["data"]
                        else:
                            raw_schema = content
                    except Exception:
                        raw_schema = content
            elif config.base_url:
                filtered_override = filter_forwardable_headers(headers_override or {}, config.allowed_forward_headers)
                fetch_headers = {"Content-Type": "application/json", **config.headers, **filtered_override}
                async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
                    resp = await client.post(
                        config.base_url,
                        json={"query": INTROSPECTION_QUERY},
                        headers=fetch_headers,
                    )
                    resp.raise_for_status()
                    res_json = resp.json()
                    if "data" in res_json:
                        raw_schema = res_json["data"]
                    else:
                        raise ValueError("Invalid GraphQL introspection response")

        if isinstance(raw_schema, dict) and "__schema" in raw_schema:
            gql_schema = build_client_schema(raw_schema)
        elif isinstance(raw_schema, str):
            gql_schema = build_schema(raw_schema)
        else:
            raise ValueError("Must provide valid GraphQL SDL string, schema file, URL, or introspection dict")

        self._servers[config.server_id] = config
        self._schemas[config.server_id] = gql_schema

        operations = GraphQLParser.parse_schema(gql_schema, config.server_id)

        verified_tools: List[ToolDefinition] = []
        op_map: Dict[str, GraphQLOperation] = {}
        tool_names: List[str] = []

        for op in operations:
            tool_def = ToolDefinition(
                name=op.tool_name,
                title=op.field_name,
                description=op.description,
                inputSchema=op.input_schema,
                outputSchema=op.output_schema,
                annotations=op.annotations,
            )

            hash_ok = hash_pinning_guard.verify_tool_schema(op.tool_name, tool_def.to_mcp_format())
            if not hash_ok:
                logger.error(f"GraphQL Tool '{op.tool_name}' from [{config.server_id}] failed SHA-256 Hash-Pinning check!")
                continue

            verified_tools.append(tool_def)
            op_map[op.tool_name] = op
            self._tool_to_server[op.tool_name] = config.server_id
            tool_names.append(op.tool_name)

            schema_registry.register_tool_definition(tool_def)

        self._operations[config.server_id] = op_map

        adapter_cfg = PlugInAdapterConfig(
            name=config.server_id,
            description=config.name or f"GraphQL Bridge for {config.base_url}",
            adapter_type=AdapterType.GRAPHQL_BRIDGE,
            base_url=config.base_url,
            tools=tool_names,
            status=AdapterStatus.RUNNING,
        )
        adapter_registry.register_adapter(adapter_cfg)

        logger.info(f"Registered GraphQL Bridge [{config.server_id}] with {len(verified_tools)} tools")
        return verified_tools

    async def call_graphql_tool(
        self,
        server_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        headers_override: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute an MCP 'tools/call' by translating it into a GraphQL query or mutation request
        sent over HTTP POST to the target GraphQL endpoint.
        """
        srv = self.get_server(server_id)
        if not srv:
            return {
                "jsonrpc": "2.0",
                "id": "graphql-error",
                "error": {"code": -32002, "message": f"GraphQL Bridge Server '{server_id}' not found in registry"},
            }

        op_map = self._operations.get(server_id, {})
        op = op_map.get(tool_name)
        if not op:
            return {
                "jsonrpc": "2.0",
                "id": "graphql-error",
                "error": {"code": -32601, "message": f"Tool '{tool_name}' not found on GraphQL Bridge '{server_id}'"},
            }

        tool_args = tool_args or {}

        custom_query = tool_args.pop("query", None) or tool_args.pop("document", None)
        custom_variables = tool_args.pop("variables", None)

        if custom_query:
            query_str = custom_query
            variables = custom_variables if isinstance(custom_variables, dict) else tool_args
        else:
            field_name = op.field_name
            gql_schema = self._schemas.get(server_id)
            return_type = None
            if gql_schema:
                if op.operation_type == "query":
                    q_type = gql_schema.query_type
                    if q_type and field_name in q_type.fields:
                        return_type = q_type.fields[field_name].type
                elif op.operation_type == "mutation":
                    m_type = gql_schema.mutation_type
                    if m_type and field_name in m_type.fields:
                        return_type = m_type.fields[field_name].type

            selection_set = ""
            if return_type:
                selection_set = GraphQLParser.generate_selection_set(return_type, depth=2)

            arg_names = list(tool_args.keys())
            if arg_names:
                param_defs = []
                arg_passing = []
                for a_name in arg_names:
                    param_defs.append(f"${a_name}: JSON")
                    arg_passing.append(f"{a_name}: ${a_name}")
                params_str = f"({', '.join(param_defs)})" if param_defs else ""
                args_str = f"({', '.join(arg_passing)})" if arg_passing else ""
                query_str = f"{op.operation_type} {op.tool_name}{params_str} {{ {field_name}{args_str} {selection_set} }}"
                variables = tool_args
            else:
                query_str = f"{op.operation_type} {{ {field_name} {selection_set} }}"
                variables = {}

        filtered_override = filter_forwardable_headers(headers_override or {}, srv.allowed_forward_headers)
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            **srv.headers,
        }
        headers.update(filtered_override)

        payload = {"query": query_str}
        if variables:
            payload["variables"] = variables

        async with httpx.AsyncClient(timeout=srv.timeout_seconds) as client:
            try:
                resp = await client.post(srv.base_url, json=payload, headers=headers)
                await resp.aread()

                metrics_collector.record_request("POST", srv.base_url, resp.status_code)

                try:
                    res_json = resp.json()
                    res_text = json.dumps(res_json, ensure_ascii=False, indent=2)
                except Exception:
                    res_text = resp.text

                if len(res_text) > MAX_RESPONSE_BYTES:
                    truncated_chars = len(res_text) - MAX_RESPONSE_BYTES
                    res_text = (
                        res_text[:MAX_RESPONSE_BYTES]
                        + f"\n\n... [TRUNCATED: Response payload reduced by {truncated_chars} chars to protect LLM context window limit]"
                    )

                mcp_content = [{"type": "text", "text": res_text}]
                result_payload = {"content": mcp_content}
                if isinstance(res_json, dict) and "data" in res_json:
                    result_payload["structuredContent"] = res_json["data"]

                if resp.is_error or (isinstance(res_json, dict) and "errors" in res_json):
                    result_payload["isError"] = True

                return {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": result_payload,
                }

            except httpx.TimeoutException:
                logger.error(f"Timeout executing GraphQL tool '{tool_name}' on endpoint [{srv.base_url}]")
                return {
                    "jsonrpc": "2.0",
                    "id": "graphql-timeout",
                    "error": {"code": -32004, "message": f"GraphQL Server '{server_id}' timed out for tool '{tool_name}'"},
                }
            except Exception as exc:
                logger.error(f"Error executing GraphQL tool '{tool_name}': {exc}")
                return {
                    "jsonrpc": "2.0",
                    "id": "graphql-error",
                    "error": {"code": -32002, "message": f"GraphQL Bridge proxy error: {exc}"},
                }


graphql_bridge_registry = GraphQLBridgeRegistry()
