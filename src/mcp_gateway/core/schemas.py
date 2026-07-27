"""
Tool JSON Schema Registry and Manager for Mock MCP Gateway.
Loads, validates, sanitizes, and exposes tool JSON schemas in standard MCP (inputSchema)
and OpenAI (function/parameters) formats with SHA-256 Hash Pinning against Rug-Pull attacks.
"""

from pathlib import Path
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from mcp.types import Tool

from mcp_gateway.observability import logger
from mcp_gateway.security.security import ToolDescriptionSanitizer, hash_pinning_guard


class ToolDefinition(Tool):
    """Tool definition subclassing native mcp.types.Tool for MCP 2026-07-28 compliance."""

    def sanitize(self) -> None:
        """Sanitize description to prevent prompt injection / tool poisoning."""
        if self.description:
            self.description = ToolDescriptionSanitizer.sanitize_description(self.description)

    def validate_schema_structure(self) -> bool:
        """Validate that inputSchema adheres to standard JSON Schema structure (type == 'object')."""
        if not isinstance(self.inputSchema, dict):
            return False
        schema_type = self.inputSchema.get("type")
        if schema_type:
            if isinstance(schema_type, str) and schema_type != "object":
                return False
            elif isinstance(schema_type, list) and "object" not in schema_type:
                return False
        return True

    def validate_arguments(self, arguments: Dict[str, Any]) -> List[str]:
        """
        Validate invocation arguments against required fields defined in inputSchema.
        Returns a list of missing required parameter field names, if any.
        """
        missing_fields = []
        if isinstance(self.inputSchema, dict):
            required_fields = self.inputSchema.get("required", [])
            for req in required_fields:
                if req not in arguments:
                    missing_fields.append(req)
        return missing_fields

    def to_mcp_format(self) -> Dict[str, Any]:
        """Convert to standard MCP tools/list format using native model dump."""
        return self.model_dump(exclude_none=True)

    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI / LLM function calling schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description or "",
                "parameters": self.inputSchema,
            },
        }


class ToolSchemaRegistry:
    """Registry managing tool JSON schemas loaded from configuration files."""

    def __init__(self, schemas_dir: Optional[Path | str] = None, catalog_file: Optional[Path | str] = None):
        self.tools: Dict[str, ToolDefinition] = {}

        # Resolve paths relative to project root or config
        if schemas_dir is None:
            schemas_dir = Path("config/schemas/tools")
        else:
            schemas_dir = Path(schemas_dir)

        if catalog_file is None:
            catalog_file = Path("config/schemas/tools.json")
        else:
            catalog_file = Path(catalog_file)

        self.load_schemas(schemas_dir=schemas_dir, catalog_file=catalog_file)

    def register_tool_definition(self, tool_def: ToolDefinition) -> None:
        """Register or update a tool definition in the registry."""
        tool_def.sanitize()
        self.tools[tool_def.name] = tool_def
        logger.info(f"Registered tool definition [{tool_def.name}] in schema registry")

    def load_schemas(self, schemas_dir: Path, catalog_file: Path) -> None:
        """Load schemas from individual JSON files or fall back to master tools.json catalog."""
        self.tools.clear()

        # 1. Load individual tool JSON files from directory if present
        if schemas_dir.exists() and schemas_dir.is_dir():
            for file_path in sorted(schemas_dir.glob("*.json")):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    tool_def = ToolDefinition.model_validate(data)
                    tool_def.sanitize()

                    # Verify or pin schema hash to defend against Rug-Pull attacks
                    mcp_payload = tool_def.to_mcp_format()
                    if hash_pinning_guard.verify_tool_schema(tool_def.name, mcp_payload):
                        self.tools[tool_def.name] = tool_def
                        logger.info(f"Loaded and verified tool schema [{tool_def.name}]: {file_path}")
                    else:
                        logger.warning(f"Skipping tampered tool schema [{tool_def.name}]: Rug-Pull detected!")
                except Exception as exc:
                    logger.error(f"Failed to load tool schema from {file_path}: {exc}")

        # 2. Fall back or supplement from catalog_file if directory yielded nothing or partial
        if catalog_file.exists() and catalog_file.is_file():
            try:
                with open(catalog_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                tools_list = data.get("tools", [])
                for item in tools_list:
                    tool_def = ToolDefinition.model_validate(item)
                    tool_def.sanitize()
                    if tool_def.name not in self.tools:
                        mcp_payload = tool_def.to_mcp_format()
                        if hash_pinning_guard.verify_tool_schema(tool_def.name, mcp_payload):
                            self.tools[tool_def.name] = tool_def
                            logger.info(f"Loaded tool schema [{tool_def.name}] from catalog: {catalog_file}")
            except Exception as exc:
                logger.error(f"Failed to load master tool catalog from {catalog_file}: {exc}")

    def get_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        """Get a single tool definition by name."""
        return self.tools.get(tool_name)

    def list_tools(
        self, filter_names: Optional[List[str]] = None, format_type: str = "mcp"
    ) -> List[Dict[str, Any]]:
        """
        List tools in specified format ('mcp', 'openai', or 'raw').
        Optionally filter by a list of tool names.
        """
        result = []
        fmt = format_type.lower()

        target_names = filter_names if (filter_names and "*" not in filter_names) else list(self.tools.keys())

        for name in target_names:
            tool_def = self.tools.get(name)
            if not tool_def:
                continue

            if fmt == "openai":
                result.append(tool_def.to_openai_format())
            elif fmt == "raw":
                result.append(tool_def.model_dump())
            else:
                result.append(tool_def.to_mcp_format())

        return result


# Global Singleton Schema Registry Instance
schema_registry = ToolSchemaRegistry()
