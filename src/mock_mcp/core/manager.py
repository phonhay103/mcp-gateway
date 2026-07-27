"""
Standalone Unified Mock MCP Service Manager.
Provides exactly one unified FastMCP server instance mounted on /mcp, with scenario resolution via headers.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio
import contextlib
import yaml
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

from mock_mcp.core.config import (
    ErrorSimulationRule,
    GatewayConfig,
    GatewayYamlConfig,
    RateLimitConfig,
    ScenarioConfig,
    ToolBehaviorConfig,
    ToolErrorConfig,
    AuthConfig,
)
from mock_mcp.core.fixtures import FixtureLoader
from mock_mcp.logger import logger, setup_logging
from mock_mcp.core.schemas import schema_registry
from mock_mcp.core.server import create_mcp_server
from mock_mcp.core.context import current_scenario_id, scenario_states, scenario_configs_map


class ServerManager:
    """
    Manager loading Scenarios via Pydantic v2 and orchestrating FastMCP servers.
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.yaml_config: Optional[GatewayYamlConfig] = None
        self.fixture_loader: Optional[FixtureLoader] = None
        self.scenario_fixture_states: Dict[str, Dict[str, Any]] = {}
        self.instances: Dict[str, FastMCP] = {}

        setup_logging()
        self.load_config()

    @property
    def gateway_host(self) -> str:
        return self.yaml_config.gateway.host if self.yaml_config else "0.0.0.0"

    @property
    def gateway_port(self) -> int:
        return self.yaml_config.gateway.port if self.yaml_config else 2027

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
        """Determine EXACTLY which tools, resources, and prompts are loaded for a scenario."""
        if sc.tools and "*" not in sc.tools:
            final_tools = list(sc.tools)
        else:
            final_tools = list(schema_registry.tools.keys())

        if sc.resources and "*" not in sc.resources:
            final_resources = list(sc.resources)
        else:
            final_resources = ["system://status", "users://list"]

        if sc.prompts and "*" not in sc.prompts:
            final_prompts = list(sc.prompts)
        else:
            final_prompts = ["code_review"]

        return final_tools, final_resources, final_prompts

    def load_config(self) -> None:
        """Read main configuration YAML and load individual scenario files, shared fixtures, and tool schemas."""
        if not self.config_path.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        try:
            self.yaml_config = GatewayYamlConfig.model_validate(raw_data)
        except Exception as exc:
            logger.error(f"Pydantic validation failed for {self.config_path}: {exc}")
            raise

        fixtures_dir = self.config_path.parent / Path(self.yaml_config.gateway.fixtures_dir).name
        if not fixtures_dir.exists():
            fixtures_dir = Path(self.yaml_config.gateway.fixtures_dir)
        self.fixture_loader = FixtureLoader(fixtures_dir=fixtures_dir)

        schemas_dir = self.config_path.parent / Path(self.yaml_config.gateway.schemas_dir).name
        if not schemas_dir.exists():
            schemas_dir = Path(self.yaml_config.gateway.schemas_dir)
        catalog_file = self.config_path.parent / "schemas" / "tools.json"
        if not catalog_file.exists():
            catalog_file = Path("configs/mock_mcp/schemas/tools.json")
        schema_registry.load_schemas(schemas_dir=schemas_dir, catalog_file=catalog_file)

        if not self.yaml_config.scenarios:
            scenarios_dir = self.config_path.parent / Path(self.yaml_config.gateway.scenarios_dir).name
            if scenarios_dir.exists():
                discovered_scenarios = self._discover_scenarios_from_folder(scenarios_dir)
                if discovered_scenarios:
                    self.yaml_config.scenarios = discovered_scenarios

            if not self.yaml_config.scenarios and "servers" in raw_data and raw_data["servers"]:
                legacy_scenarios = []
                for srv_item in raw_data["servers"]:
                    if isinstance(srv_item, dict):
                        sc_model = ScenarioConfig.model_validate(srv_item)
                        legacy_scenarios.append(sc_model)
                self.yaml_config.scenarios = legacy_scenarios

        if not self.scenario_configs:
            logger.error("No scenarios discovered or configured.")
            raise ValueError("No scenarios discovered or configured.")

        self.scenario_fixture_states.clear()
        self.instances.clear()

        # Initialize shared context structures
        scenario_states.clear()
        scenario_configs_map.clear()

        for sc in self.scenario_configs:
            fixture_state = self.fixture_loader.load_fixtures(sc.fixtures) if sc.fixtures else {}

            tb_data = {}
            if sc.tool_behaviors:
                tb_data = {t_name: tb.model_dump() for t_name, tb in sc.tool_behaviors.items()}
            elif sc.error_simulations:
                for sim in sc.error_simulations:
                    tb_data[sim.tool] = {
                        "error": {
                            "is_error": sim.is_error,
                            "message": sim.message,
                            "code": None,
                            "details": None,
                        }
                    }

            if tb_data:
                fixture_state["_tool_behaviors"] = tb_data

            self.scenario_fixture_states[sc.id] = fixture_state
            scenario_states[sc.id] = fixture_state
            scenario_configs_map[sc.id] = sc

            tools, resources, prompts = self._resolve_scenario_capabilities(sc)

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
            f"and {len(schema_registry.tools)} Tool Schemas"
        )

    def create_mock_app(self) -> Starlette:
        """
        Create a Starlette ASGI application for the unified Mock MCP Service.
        """
        default_sc_id = self.scenario_configs[0].id if self.scenario_configs else "default_scenario"

        # Create exactly 1 master FastMCP instance serving all potential mock tools
        master_instance = create_mcp_server(
            name="Mock MCP Server",
            description="Unified Standalone Mock MCP Server with dynamic tool selection via scenario header",
            tools=["*"],
            resources=["*"],
            prompts=["*"],
        )
        default_sub_app = master_instance.http_app(transport="streamable-http")

        @contextlib.asynccontextmanager
        async def mock_lifespan(app_instance: Starlette):
            async with contextlib.AsyncExitStack() as stack:
                if hasattr(default_sub_app, "lifespan") and callable(default_sub_app.lifespan):
                    await stack.enter_async_context(default_sub_app.lifespan(default_sub_app))

                import os
                from mock_mcp.core.auto_register import register_scenarios_with_gateway

                gateway_url = os.getenv("GATEWAY_REGISTER_URL")
                if gateway_url:
                    mock_service_url = os.getenv("MOCK_SERVICE_URL", "http://mock-mcp:2027")
                    api_key = os.getenv("GATEWAY_API_KEY", "admin-secret-key-123")
                    asyncio.create_task(
                        register_scenarios_with_gateway(
                            gateway_url=gateway_url,
                            scenarios=self.scenario_configs,
                            mock_service_url=mock_service_url,
                            api_key=api_key,
                        )
                    )

                yield

        async def homepage(request):
            return JSONResponse({
                "service": "mock_mcp",
                "status": "online",
                "endpoint": "/mcp",
                "scenarios": [sc.id for sc in self.scenario_configs],
                "note": "Unified Mock MCP Server. Pass X-MCP-Scenario header to select active scenario."
            })

        async def health(request):
            return JSONResponse({"status": "healthy"})

        async def custom_mcp_endpoint(request):
            """Unified endpoint routing request, dynamically filtering tools and error simulation based on active scenario."""
            headers = dict(request.headers)
            active_sc = headers.get("x-mcp-scenario", default_sc_id)

            # Restrict active_sc to a registered scenario
            if active_sc not in scenario_configs_map:
                active_sc = default_sc_id

            token = current_scenario_id.set(active_sc)
            try:
                # Intercept JSON-RPC payload for tools/list and tools/call
                if request.method == "POST":
                    try:
                        body = await request.json()
                        method = body.get("method")
                        req_id = body.get("id")

                        if method == "tools/list":
                            sc = scenario_configs_map.get(active_sc)
                            all_tools = schema_registry.tools
                            allowed_names = sc.tools if sc and sc.tools else list(all_tools.keys())

                            filtered_tools = []
                            for name in allowed_names:
                                tool_def = all_tools.get(name)
                                if tool_def:
                                    filtered_tools.append(tool_def.to_mcp_format())

                            return JSONResponse({
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "result": {
                                    "tools": filtered_tools
                                }
                            })

                        elif method == "tools/call":
                            params = body.get("params", {})
                            tool_name = params.get("name")
                            sc = scenario_configs_map.get(active_sc)

                            if sc and sc.tools and tool_name not in sc.tools:
                                return JSONResponse({
                                    "jsonrpc": "2.0",
                                    "id": req_id,
                                    "error": {
                                        "code": -32601,
                                        "message": f"Tool '{tool_name}' is not enabled in active scenario '{active_sc}'"
                                    }
                                })

                    except Exception as exc:
                        logger.error(f"Error parsing custom JSON-RPC endpoint: {exc}")

                # Forward standard execution to FastMCP sub-app
                return await default_sub_app(request.scope, request.receive, request.send)

            finally:
                current_scenario_id.reset(token)

        routes = [
            Route("/", homepage),
            Route("/health", health),
            Route("/mcp", custom_mcp_endpoint, methods=["GET", "POST"]),
        ]
        app = Starlette(routes=routes, lifespan=mock_lifespan)
        return app

    def run_gateway(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """Run the single unified Mock MCP Service on port 2027."""
        target_host = host or self.gateway_host
        target_port = port or 2027

        logger.info(f"=== Starting Standalone Unified Mock MCP Service on {target_host}:{target_port} ===")
        logger.info(f" -> Mock Endpoint: http://{target_host}:{target_port}/mcp")

        app = self.create_mock_app()
        uvicorn.run(app, host=target_host, port=target_port)
