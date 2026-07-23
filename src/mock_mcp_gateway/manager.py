"""
Multi-MCP Scenario Manager and Gateway Orchestrator with Decoupled Architecture & Pydantic v2 Models.
MCP Servers are pure, scenario-agnostic definitions in config/mcp_servers.yaml.
Scenarios in config/scenarios/*.yaml dictate EXACTLY which tools, resources, and shared fixtures get loaded.
Provides scenario management (/scenarios, /scenarios/select, /scenarios/clear), session lifecycle (/sessions, /sessions/close),
and Tool JSON Schema catalog endpoints (/tools, /tools/{tool_name}, /scenarios/{scenario_id}/tools).
Enforces Session Isolation via X-Session-ID, Auth, Rate Limiting & OWASP Headers across concurrent executions.
Generates time-sortable UUID v7 for session identifiers.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import asyncio
import os
import uuid
import yaml
from fastmcp import FastMCP
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from mock_mcp_gateway.auth import AuthConfig, AuthMiddleware
from mock_mcp_gateway.fixtures import FixtureLoader
from mock_mcp_gateway.logger import logger, setup_logging
from mock_mcp_gateway.rate_limiter import RateLimitMiddleware
from mock_mcp_gateway.schemas import schema_registry
from mock_mcp_gateway.security import EnterpriseSecurityMiddleware
from mock_mcp_gateway.session import SessionMiddleware, generate_uuid7, session_manager
from mock_mcp_gateway.server import create_mcp_server


class RateLimitConfig(BaseModel):
    """Pydantic v2 Model for Gateway Rate Limiting Configuration."""

    enabled: bool = Field(default=True, description="Enable sliding window rate limiting")
    requests_per_minute: int = Field(default=120, description="Max requests per minute")

    model_config = {"extra": "ignore"}


class GatewayConfig(BaseModel):
    """Pydantic v2 Model for Gateway Global Configuration."""

    host: str = Field(default="0.0.0.0", description="Host address")
    port: int = Field(default=8000, description="Shared gateway port")
    enabled: bool = Field(default=True, description="Enable gateway mode")
    header_session_name: str = Field(default="X-Session-ID", description="Header for session tracking")
    header_scenario_name: str = Field(default="X-MCP-Scenario", description="Header for scenario selection")
    scenarios_dir: str = Field(default="config/scenarios", description="Directory containing scenario YAML files")
    fixtures_dir: str = Field(default="config/fixtures", description="Directory containing fixture YAML files")
    schemas_dir: str = Field(default="config/schemas/tools", description="Directory containing tool schema JSON files")
    rate_limiting: RateLimitConfig = Field(default_factory=RateLimitConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)

    model_config = {"extra": "ignore"}


class BaseMCPServerConfig(BaseModel):
    """Pydantic v2 Model for Decoupled Base MCP Server Definitions (Scenario-Agnostic)."""

    id: str = Field(default="", description="Unique MCP Server ID")
    name: str = Field(default="", description="Server Name")
    description: str = Field(default="", description="Server Description")
    tools: List[str] = Field(default_factory=list, description="Base Tools provided by this MCP Server")
    resources: List[str] = Field(default_factory=list, description="Base Resources provided by this MCP Server")
    prompts: List[str] = Field(default_factory=list, description="Base Prompts provided by this MCP Server")

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def ensure_id_and_name(self) -> "BaseMCPServerConfig":
        if not self.id and self.name:
            self.id = self.name.lower().replace(" ", "-")
        elif not self.name and self.id:
            self.name = self.id
        return self


class ScenarioConfig(BaseModel):
    """
    Pydantic v2 Model for Individual Scenario Definitions.
    Scenarios dictate EXACTLY which tools, resources, and prompts are loaded.
    """

    id: str = Field(default="", description="Unique Scenario ID")
    name: str = Field(default="", description="Human-readable scenario name")
    description: str = Field(default="", description="Scenario description")
    transport: str = Field(default="http", description="MCP transport protocol ('http' or 'streamable-http')")
    host: str = Field(default="0.0.0.0", description="Host address for standalone mode")
    port: int = Field(default=8001, description="Dedicated port for standalone mode")
    prefix: str = Field(default="", description="URL prefix when mounted in Gateway")
    enable_gateway: bool = Field(default=True, description="Mount on shared gateway port")
    enable_standalone: bool = Field(default=True, description="Run on dedicated standalone port")
    servers: List[str] = Field(default_factory=list, description="Referenced Base MCP Server IDs")
    fixtures: List[str] = Field(default_factory=list, description="Referenced Fixture filenames")
    tools: List[str] = Field(default_factory=list, description="Explicit tools selection for this scenario")
    resources: List[str] = Field(default_factory=list, description="Explicit resources selection for this scenario")
    prompts: List[str] = Field(default_factory=list, description="Explicit prompts selection for this scenario")

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def ensure_id_and_prefix(self) -> "ScenarioConfig":
        if not self.id and self.name:
            self.id = self.name.lower().replace(" ", "-")
        elif not self.name and self.id:
            self.name = self.id

        if not self.prefix and self.id:
            self.prefix = f"/{self.id.lower().replace(' ', '-')}"
        elif not self.prefix:
            self.prefix = "/scenario"

        if not self.prefix.startswith("/"):
            self.prefix = "/" + self.prefix

        return self


# Backwards compatibility alias for ServerConfig
ServerConfig = ScenarioConfig


class GatewayYamlConfig(BaseModel):
    """Pydantic v2 Root Model for entire `mcp_servers.yaml` configuration."""

    version: str = Field(default="3.0", description="Configuration schema version")
    description: str = Field(default="", description="Configuration file description")
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    servers: List[BaseMCPServerConfig] = Field(default_factory=list, description="Decoupled Base MCP Servers")
    scenarios: List[ScenarioConfig] = Field(default_factory=list, description="Scenarios")

    model_config = {"extra": "ignore"}


class ServerManager:
    """
    Manager loading Base MCP Servers & Scenarios via Pydantic v2 and orchestrating FastMCP servers.
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.yaml_config: Optional[GatewayYamlConfig] = None
        self.base_servers: Dict[str, BaseMCPServerConfig] = {}
        self.fixture_loader: Optional[FixtureLoader] = None
        self.scenario_fixture_states: Dict[str, Dict[str, Any]] = {}
        self.instances: Dict[str, FastMCP] = {}

        # Initialize Loguru logger
        setup_logging()
        self.load_config()

    @property
    def gateway_host(self) -> str:
        return self.yaml_config.gateway.host if self.yaml_config else "0.0.0.0"

    @property
    def gateway_port(self) -> int:
        return self.yaml_config.gateway.port if self.yaml_config else 8000

    @property
    def gateway_enabled(self) -> bool:
        return self.yaml_config.gateway.enabled if self.yaml_config else True

    @property
    def rate_limit_enabled(self) -> bool:
        return self.yaml_config.gateway.rate_limiting.enabled if self.yaml_config else True

    @property
    def rate_limit_rpm(self) -> int:
        return self.yaml_config.gateway.rate_limiting.requests_per_minute if self.yaml_config else 120

    @property
    def auth_config(self) -> AuthConfig:
        return self.yaml_config.gateway.auth if self.yaml_config else AuthConfig()

    @property
    def scenario_configs(self) -> List[ScenarioConfig]:
        return self.yaml_config.scenarios if self.yaml_config else []

    # Backwards compatibility property
    @property
    def server_configs(self) -> List[ScenarioConfig]:
        return self.scenario_configs

    def _discover_scenarios_from_folder(self, scenarios_dir_path: Path) -> List[ScenarioConfig]:
        """Scan directory and parse individual scenario YAML files."""
        discovered = []
        if not scenarios_dir_path.exists():
            return discovered

        for file_path in sorted(scenarios_dir_path.glob("*.yaml")):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    sc_data = yaml.safe_load(f) or {}
                if sc_data:
                    sc_model = ScenarioConfig.model_validate(sc_data)
                    discovered.append(sc_model)
                    logger.info(f"Loaded scenario file [{sc_model.id}]: {file_path}")
            except Exception as exc:
                logger.error(f"Failed to load scenario file {file_path}: {exc}")

        return discovered

    def _resolve_scenario_capabilities(self, sc: ScenarioConfig):
        """
        Determine EXACTLY which tools, resources, and prompts are loaded for a scenario.
        - If sc.tools is specified (and not wildcard ["*"]), it acts as an explicit exact whitelist.
        - If sc.tools is empty or ["*"], inherit tools from referenced base servers.
        """
        # 1. Resolve Tools
        if sc.tools and "*" not in sc.tools:
            final_tools = list(sc.tools)
        else:
            final_tools = []
            for server_id in sc.servers:
                base_srv = self.base_servers.get(server_id)
                if base_srv:
                    for t in base_srv.tools:
                        if t not in final_tools:
                            final_tools.append(t)
                else:
                    logger.warning(f"Scenario [{sc.id}] references unknown Base MCP Server [{server_id}]")

        # 2. Resolve Resources
        if sc.resources and "*" not in sc.resources:
            final_resources = list(sc.resources)
        else:
            final_resources = []
            for server_id in sc.servers:
                base_srv = self.base_servers.get(server_id)
                if base_srv:
                    for r in base_srv.resources:
                        if r not in final_resources:
                            final_resources.append(r)

        # 3. Resolve Prompts
        if sc.prompts and "*" not in sc.prompts:
            final_prompts = list(sc.prompts)
        else:
            final_prompts = []
            for server_id in sc.servers:
                base_srv = self.base_servers.get(server_id)
                if base_srv:
                    for p in base_srv.prompts:
                        if p not in final_prompts:
                            final_prompts.append(p)

        return final_tools, final_resources, final_prompts

    def load_config(self) -> None:
        """Read main configuration YAML and load individual scenario files, shared fixtures, and tool schemas."""
        if not self.config_path.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        # Validate main configuration with Pydantic v2
        try:
            self.yaml_config = GatewayYamlConfig.model_validate(raw_data)
        except Exception as exc:
            logger.error(f"Pydantic validation failed for {self.config_path}: {exc}")
            raise

        # Store Base MCP Servers lookup
        self.base_servers = {srv.id: srv for srv in self.yaml_config.servers}

        # Setup Fixture Loader relative to config path
        fixtures_dir = self.config_path.parent / Path(self.yaml_config.gateway.fixtures_dir).name
        if not fixtures_dir.exists():
            fixtures_dir = Path(self.yaml_config.gateway.fixtures_dir)
        self.fixture_loader = FixtureLoader(fixtures_dir=fixtures_dir)

        # Load tool schemas from configured schemas directory
        schemas_dir = self.config_path.parent / Path(self.yaml_config.gateway.schemas_dir).relative_to("config") if "config" in self.yaml_config.gateway.schemas_dir else Path(self.yaml_config.gateway.schemas_dir)
        catalog_file = self.config_path.parent / "schemas" / "tools.json"
        schema_registry.load_schemas(schemas_dir=schemas_dir, catalog_file=catalog_file)

        # Auto-discover scenarios from folder if explicit scenarios were not provided in main YAML
        if not self.yaml_config.scenarios:
            scenarios_dir = self.config_path.parent / Path(self.yaml_config.gateway.scenarios_dir).name
            if scenarios_dir.exists():
                discovered_scenarios = self._discover_scenarios_from_folder(scenarios_dir)
                if discovered_scenarios:
                    self.yaml_config.scenarios = discovered_scenarios

            if not self.yaml_config.scenarios and "servers" in raw_data and raw_data["servers"]:
                # Legacy fallback if no scenarios folder existed relative to config
                legacy_scenarios = []
                for srv_item in raw_data["servers"]:
                    if isinstance(srv_item, dict):
                        sc_model = ScenarioConfig.model_validate(srv_item)
                        legacy_scenarios.append(sc_model)
                self.yaml_config.scenarios = legacy_scenarios

        if not self.scenario_configs:
            logger.error("No scenarios discovered or configured.")
            raise ValueError("No scenarios discovered or configured.")

        # Resolve capabilities & shared fixtures per scenario
        self.scenario_fixture_states.clear()
        self.instances.clear()

        for sc in self.scenario_configs:
            # Load referenced fixtures
            fixture_state = self.fixture_loader.load_fixtures(sc.fixtures) if sc.fixtures else {}
            self.scenario_fixture_states[sc.id] = fixture_state

            # Resolve exact tools selection for this scenario
            tools, resources, prompts = self._resolve_scenario_capabilities(sc)

            # Instantiate FastMCP server per scenario with exact tools loaded
            mcp_instance = create_mcp_server(
                name=sc.id,
                description=sc.description,
                tools=tools,
                resources=resources,
                prompts=prompts,
            )
            self.instances[sc.id] = mcp_instance

        logger.info(
            f"Successfully loaded {len(self.scenario_configs)} Scenarios "
            f"composing capabilities from {len(self.base_servers)} Base MCP Servers "
            f"and {len(schema_registry.tools)} Tool Schemas"
        )

    def _apply_middleware_pipeline(
        self,
        app: Starlette,
        scenario_id: str = "default_scenario",
        server_prefix_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """Apply Enterprise Middlewares (Session -> Auth -> Rate Limiting -> OWASP Security Headers)."""
        app.add_middleware(EnterpriseSecurityMiddleware)
        app.add_middleware(
            RateLimitMiddleware,
            requests_per_minute=self.rate_limit_rpm,
            enabled=self.rate_limit_enabled,
        )
        if self.auth_config.enabled:
            app.add_middleware(
                AuthMiddleware,
                auth_config=self.auth_config,
                server_prefix_map=server_prefix_map,
            )

        # Session isolation middleware for scenario context and fixture state
        header_session = self.yaml_config.gateway.header_session_name if self.yaml_config else "X-Session-ID"
        header_scenario = self.yaml_config.gateway.header_scenario_name if self.yaml_config else "X-MCP-Scenario"
        fixture_state = self.scenario_fixture_states.get(scenario_id, {})
        app.add_middleware(
            SessionMiddleware,
            header_session_name=header_session,
            header_scenario_name=header_scenario,
            default_scenario_id=scenario_id,
            default_server_id=scenario_id,
            initial_fixture_state=fixture_state,
        )

    def create_gateway_app(self) -> Starlette:
        """
        Create a unified Starlette ASGI application mounting configured Scenarios with Middlewares and API management routes.
        """
        gateway_scenarios = [sc for sc in self.scenario_configs if sc.enable_gateway]
        prefix_map = {sc.prefix: sc.id for sc in gateway_scenarios}

        async def homepage(request):
            session_id = getattr(request.state, "session_id", "N/A")
            status_data = {
                "status": "online",
                "mode": "enterprise-scenario-gateway",
                "session_id": session_id,
                "shared_port": self.gateway_port,
                "security": {
                    "auth_enabled": self.auth_config.enabled,
                    "auth_type": self.auth_config.auth_type,
                    "rate_limiting_enabled": self.rate_limit_enabled,
                    "rate_limit_rpm": self.rate_limit_rpm,
                    "session_tracking_header": self.yaml_config.gateway.header_session_name,
                    "scenario_tracking_header": self.yaml_config.gateway.header_scenario_name,
                    "query_param_auth": "disabled",
                },
                "scenario_count": len(gateway_scenarios),
                "endpoints": {
                    "list_scenarios": "/scenarios",
                    "select_scenario": "/scenarios/select",
                    "clear_scenario": "/scenarios/clear",
                    "list_sessions": "/sessions",
                    "create_session": "/sessions",
                    "close_session": "/sessions/{session_id}",
                    "list_tools": "/tools",
                    "get_tool": "/tools/{tool_name}",
                    "scenario_tools": "/scenarios/{scenario_id}/tools",
                },
            }
            return JSONResponse(status_data)

        async def list_scenarios_endpoint(request):
            """GET /scenarios: List all available scenarios."""
            session_id = getattr(request.state, "session_id", "N/A")
            sc_list = []
            for sc in gateway_scenarios:
                tools, resources, prompts = self._resolve_scenario_capabilities(sc)
                endpoint = f"{sc.prefix}/mcp"
                sc_list.append({
                    "id": sc.id,
                    "name": sc.name,
                    "description": sc.description,
                    "transport": sc.transport,
                    "prefix": sc.prefix,
                    "endpoint": endpoint,
                    "dedicated_port": sc.port,
                    "referenced_servers": sc.servers,
                    "fixtures": sc.fixtures,
                    "tools": tools,
                    "resources": resources,
                    "prompts": prompts,
                })
            return JSONResponse({
                "status": "success",
                "count": len(sc_list),
                "session_id": session_id,
                "scenarios": sc_list,
            })

        async def select_scenario_endpoint(request):
            """POST /scenarios/select: Bind a session to a specific scenario."""
            session_id = getattr(request.state, "session_id", "N/A")
            try:
                data = await request.json()
            except Exception:
                data = {}

            target_scenario_id = data.get("scenario_id") or request.headers.get("X-MCP-Scenario")
            target_session_id = data.get("session_id") or session_id

            if not target_scenario_id:
                return JSONResponse(
                    {"error": "Bad Request", "message": "Missing 'scenario_id' in JSON body or 'X-MCP-Scenario' header."},
                    status_code=400,
                )

            scenario_obj = next((sc for sc in self.scenario_configs if sc.id == target_scenario_id), None)
            if not scenario_obj:
                return JSONResponse(
                    {"error": "Not Found", "message": f"Scenario '{target_scenario_id}' not found."},
                    status_code=404,
                )

            # Seed session state with scenario fixtures
            fixture_state = self.scenario_fixture_states.get(target_scenario_id, {})
            session_manager.get_or_create_session(
                session_id=target_session_id,
                scenario_id=target_scenario_id,
                server_id=target_scenario_id,
                initial_state=fixture_state,
            )

            tools, resources, prompts = self._resolve_scenario_capabilities(scenario_obj)
            return JSONResponse({
                "status": "selected",
                "session_id": target_session_id,
                "active_scenario": {
                    "id": scenario_obj.id,
                    "name": scenario_obj.name,
                    "prefix": scenario_obj.prefix,
                    "endpoint": f"{scenario_obj.prefix}/mcp",
                    "tools": tools,
                    "resources": resources,
                    "prompts": prompts,
                },
            })

        async def clear_scenario_endpoint(request):
            """POST /scenarios/clear: Reset session state for a given session / scenario."""
            session_id = getattr(request.state, "session_id", "N/A")
            try:
                data = await request.json()
            except Exception:
                data = {}

            target_session_id = data.get("session_id") or session_id
            target_scenario_id = data.get("scenario_id")

            cleared_count = session_manager.clear_session(target_session_id, target_scenario_id)

            return JSONResponse({
                "status": "cleared",
                "session_id": target_session_id,
                "cleared_scenario_id": target_scenario_id or "all",
                "cleared_records": cleared_count,
            })

        async def list_sessions_endpoint(request):
            """GET /sessions: List all active sessions and their bound scenarios."""
            active_sessions = session_manager.list_active_sessions()
            return JSONResponse({
                "status": "success",
                "count": len(active_sessions),
                "sessions": active_sessions,
            })

        async def create_session_endpoint(request):
            """POST /sessions: Explicitly initialize/create a new isolated session (auto-generates UUID v7)."""
            try:
                data = await request.json()
            except Exception:
                data = {}

            new_session_id = data.get("session_id") or generate_uuid7()
            scenario_id = data.get("scenario_id") or "default_scenario"
            metadata = data.get("metadata", {})

            fixture_state = self.scenario_fixture_states.get(scenario_id, {})
            session_data = session_manager.get_or_create_session(
                session_id=new_session_id,
                scenario_id=scenario_id,
                server_id=scenario_id,
                initial_state=fixture_state,
                metadata=metadata,
            )

            return JSONResponse(
                {
                    "status": "created",
                    "session_id": session_data.session_id,
                    "scenario_id": session_data.scenario_id,
                    "created_at": session_data.created_at,
                    "metadata": session_data.metadata,
                },
                status_code=201,
            )

        async def close_session_endpoint(request):
            """DELETE /sessions/{session_id} or POST /sessions/close: Close & evict session state."""
            session_id = request.path_params.get("session_id")
            if not session_id:
                try:
                    data = await request.json()
                    session_id = data.get("session_id")
                except Exception:
                    session_id = None

            if not session_id:
                return JSONResponse(
                    {"error": "Bad Request", "message": "Missing 'session_id' in path or JSON body."},
                    status_code=400,
                )

            evicted_records = session_manager.close_session(session_id)
            return JSONResponse({
                "status": "closed",
                "session_id": session_id,
                "evicted_records": evicted_records,
            })

        async def list_tools_endpoint(request):
            """GET /tools: List tool JSON schemas. Query params: format ('mcp', 'openai', 'raw'), scenario_id."""
            fmt = request.query_params.get("format", "mcp")
            scenario_id = request.query_params.get("scenario_id")

            filter_tools = None
            if scenario_id:
                sc_obj = next((sc for sc in self.scenario_configs if sc.id == scenario_id), None)
                if sc_obj:
                    filter_tools, _, _ = self._resolve_scenario_capabilities(sc_obj)
                else:
                    return JSONResponse(
                        {"error": "Not Found", "message": f"Scenario '{scenario_id}' not found."},
                        status_code=404,
                    )

            tools_list = schema_registry.list_tools(filter_names=filter_tools, format_type=fmt)
            return JSONResponse({
                "status": "success",
                "format": fmt,
                "count": len(tools_list),
                "scenario_id": scenario_id or "all",
                "tools": tools_list,
            })

        async def get_tool_endpoint(request):
            """GET /tools/{tool_name}: Retrieve JSON schema for a specific tool by name."""
            tool_name = request.path_params.get("tool_name")
            fmt = request.query_params.get("format", "mcp")
            tool_def = schema_registry.get_tool(tool_name)
            if not tool_def:
                return JSONResponse(
                    {"error": "Not Found", "message": f"Tool '{tool_name}' not found in schema registry."},
                    status_code=404,
                )

            if fmt == "openai":
                return JSONResponse(tool_def.to_openai_format())
            elif fmt == "raw":
                return JSONResponse(tool_def.model_dump())

            return JSONResponse(tool_def.to_mcp_format())

        async def scenario_tools_endpoint(request):
            """GET /scenarios/{scenario_id}/tools: Retrieve tool JSON schemas for a specific scenario whitelist."""
            scenario_id = request.path_params.get("scenario_id")
            fmt = request.query_params.get("format", "mcp")

            sc_obj = next((sc for sc in self.scenario_configs if sc.id == scenario_id), None)
            if not sc_obj:
                return JSONResponse(
                    {"error": "Not Found", "message": f"Scenario '{scenario_id}' not found."},
                    status_code=404,
                )

            filter_tools, _, _ = self._resolve_scenario_capabilities(sc_obj)
            tools_list = schema_registry.list_tools(filter_names=filter_tools, format_type=fmt)
            return JSONResponse({
                "status": "success",
                "scenario_id": scenario_id,
                "format": fmt,
                "count": len(tools_list),
                "tools": tools_list,
            })

        routes = [
            Route("/", homepage, methods=["GET"]),
            Route("/scenarios", list_scenarios_endpoint, methods=["GET"]),
            Route("/scenarios/select", select_scenario_endpoint, methods=["POST"]),
            Route("/scenarios/clear", clear_scenario_endpoint, methods=["POST"]),
            Route("/scenarios/{scenario_id}/tools", scenario_tools_endpoint, methods=["GET"]),
            Route("/sessions", list_sessions_endpoint, methods=["GET"]),
            Route("/sessions", create_session_endpoint, methods=["POST"]),
            Route("/sessions/close", close_session_endpoint, methods=["POST"]),
            Route("/sessions/{session_id}", close_session_endpoint, methods=["DELETE"]),
            Route("/tools", list_tools_endpoint, methods=["GET"]),
            Route("/tools/{tool_name}", get_tool_endpoint, methods=["GET"]),
        ]
        app = Starlette(routes=routes)

        # Mount each enabled scenario under its configured prefix
        for sc in gateway_scenarios:
            instance = self.instances[sc.id]
            sub_app = instance.http_app(transport=sc.transport)

            # Apply session middleware specific to sub-app scenario context
            fixture_state = self.scenario_fixture_states.get(sc.id, {})
            header_session = self.yaml_config.gateway.header_session_name if self.yaml_config else "X-Session-ID"
            header_scenario = self.yaml_config.gateway.header_scenario_name if self.yaml_config else "X-MCP-Scenario"
            sub_app.add_middleware(
                SessionMiddleware,
                header_session_name=header_session,
                header_scenario_name=header_scenario,
                default_scenario_id=sc.id,
                default_server_id=sc.id,
                initial_fixture_state=fixture_state,
            )

            app.mount(sc.prefix, sub_app)

        # Attach Global Enterprise Middlewares
        self._apply_middleware_pipeline(app, scenario_id="gateway_index", server_prefix_map=prefix_map)

        return app

    def get_standalone_app(self, sc: ScenarioConfig) -> Starlette:
        """Get an ASGI app for a dedicated port scenario, wrapped with Enterprise Middlewares."""
        instance = self.instances[sc.id]
        sub_app = instance.http_app(transport=sc.transport)

        app = Starlette()
        app.mount("/", sub_app)
        prefix_map = {"/": sc.id}

        self._apply_middleware_pipeline(app, scenario_id=sc.id, server_prefix_map=prefix_map)
        return app

    def run_gateway(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """Run all Scenarios under a single HTTP Gateway process (Shared Port)."""
        target_host = host or self.gateway_host
        target_port = port or self.gateway_port

        logger.info(f"=== Starting Enterprise Scenario Gateway (Shared Port {target_port}) [Auth: {self.auth_config.enabled}] ===")
        for sc in self.scenario_configs:
            if sc.enable_gateway:
                endpoint = f"{sc.prefix}/mcp"
                logger.info(f" -> Scenario Endpoint [{sc.id}]: http://{target_host}:{target_port}{endpoint}")

        app = self.create_gateway_app()
        uvicorn.run(app, host=target_host, port=target_port)

    async def run_multi_port_async(self) -> None:
        """Run each Scenario on its own dedicated port concurrently."""
        logger.info(f"=== Starting Enterprise MCP Scenarios (Dedicated Ports) [Auth: {self.auth_config.enabled}] ===")
        tasks = []
        for sc in self.scenario_configs:
            if sc.enable_standalone:
                app = self.get_standalone_app(sc)
                endpoint = "/mcp"
                logger.info(f" -> Scenario Port [{sc.id}]: http://{sc.host}:{sc.port}{endpoint}")
                uv_config = uvicorn.Config(app, host=sc.host, port=sc.port, log_level="info")
                uv_server = uvicorn.Server(uv_config)
                tasks.append(uv_server.serve())

        if tasks:
            await asyncio.gather(*tasks)

    def run_multi_port(self) -> None:
        """Synchronous wrapper to run dedicated multi-port scenario servers."""
        asyncio.run(self.run_multi_port_async())

    async def run_hybrid_async(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """
        Run BOTH Shared Gateway (port 8000) AND Dedicated Scenario Ports (8001, 8002...) simultaneously.
        """
        target_host = host or self.gateway_host
        target_port = port or self.gateway_port

        logger.info(f"=== Starting Enterprise Hybrid Scenario Gateway (Shared Port {target_port} + Dedicated Scenario Ports) ===")

        # 1. Prepare Gateway App Server
        gateway_app = self.create_gateway_app()
        gw_config = uvicorn.Config(gateway_app, host=target_host, port=target_port, log_level="info")
        gw_server = uvicorn.Server(gw_config)
        tasks = [gw_server.serve()]

        for sc in self.scenario_configs:
            if sc.enable_gateway:
                endpoint = f"{sc.prefix}/mcp"
                logger.info(f" -> Scenario Gateway [{sc.id}]: http://{target_host}:{target_port}{endpoint}")

        # 2. Prepare Standalone Scenario Servers
        for sc in self.scenario_configs:
            if sc.enable_standalone:
                app = self.get_standalone_app(sc)
                endpoint = "/mcp"
                logger.info(f" -> Scenario Port    [{sc.id}]: http://{sc.host}:{sc.port}{endpoint}")
                uv_config = uvicorn.Config(app, host=sc.host, port=sc.port, log_level="info")
                uv_server = uvicorn.Server(uv_config)
                tasks.append(uv_server.serve())

        await asyncio.gather(*tasks)

    def run_hybrid(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """Synchronous wrapper to run hybrid scenario mode."""
        asyncio.run(self.run_hybrid_async(host=host, port=port))
