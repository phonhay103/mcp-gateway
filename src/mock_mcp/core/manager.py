"""
Multi-MCP Scenario Manager and Gateway Orchestrator with Decoupled Architecture.
Orchestrates FastMCP instances, applies Enterprise Middlewares, and mounts Control Plane & Data Plane endpoints.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio
import contextlib
import yaml
from fastmcp import FastMCP
from starlette.applications import Starlette
import uvicorn

from mock_mcp.core.adapters import AdapterStatus, AdapterType, PlugInAdapterConfig, adapter_registry
from mock_mcp.core.config import (
    BaseMCPServerConfig,
    ErrorSimulationRule,
    GatewayConfig,
    GatewayYamlConfig,
    RateLimitConfig,
    ScenarioConfig,
    ServerConfig,
    ToolBehaviorConfig,
    ToolErrorConfig,
    AuthConfig,
)
from mock_mcp.core.fixtures import FixtureLoader
from mock_mcp.logger import logger, setup_logging
from mock_mcp.core.schemas import schema_registry
from mock_mcp.core.session import SessionMiddleware, session_manager
from mock_mcp.core.server import create_mcp_server


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

        setup_logging()
        self.load_config()

    @property
    def gateway_host(self) -> str:
        return self.yaml_config.gateway.host if self.yaml_config else "0.0.0.0"

    @property
    def gateway_port(self) -> int:
        return self.yaml_config.gateway.port if self.yaml_config else 2026

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
            final_tools = []
            for server_id in sc.servers:
                base_srv = self.base_servers.get(server_id)
                if base_srv:
                    for t in base_srv.tools:
                        if t not in final_tools:
                            final_tools.append(t)
                else:
                    logger.warning(f"Scenario [{sc.id}] references unknown Base MCP Server [{server_id}]")

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

        try:
            self.yaml_config = GatewayYamlConfig.model_validate(raw_data)
        except Exception as exc:
            logger.error(f"Pydantic validation failed for {self.config_path}: {exc}")
            raise

        self.base_servers = {srv.id: srv for srv in self.yaml_config.servers}

        fixtures_dir = self.config_path.parent / Path(self.yaml_config.gateway.fixtures_dir).name
        if not fixtures_dir.exists():
            fixtures_dir = Path(self.yaml_config.gateway.fixtures_dir)
        self.fixture_loader = FixtureLoader(fixtures_dir=fixtures_dir)

        schemas_dir = self.config_path.parent / Path(self.yaml_config.gateway.schemas_dir).relative_to("config") if "config" in self.yaml_config.gateway.schemas_dir else Path(self.yaml_config.gateway.schemas_dir)
        catalog_file = self.config_path.parent / "schemas" / "tools.json"
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

            tools, resources, prompts = self._resolve_scenario_capabilities(sc)

            mcp_instance = create_mcp_server(
                name=sc.id,
                description=sc.description,
                tools=tools,
                resources=resources,
                prompts=prompts,
            )
            self.instances[sc.id] = mcp_instance

            # Register as Plug-In Adapter in Control Plane
            adapter_cfg = PlugInAdapterConfig(
                name=sc.id,
                description=sc.description,
                adapter_type=AdapterType.SCENARIO,
                tools=tools,
                resources=resources,
                prompts=prompts,
                fixtures=sc.fixtures,
                status=AdapterStatus.RUNNING,
            )
            adapter_registry.register_adapter(adapter_cfg)

        logger.info(
            f"Successfully loaded {len(self.scenario_configs)} Scenarios "
            f"composing capabilities from {len(self.base_servers)} Base MCP Servers "
            f"and {len(schema_registry.tools)} Tool Schemas"
        )

    def create_mock_app(self) -> Starlette:
        """
        Create a Starlette ASGI application for Mock MCP Service.
        Serves a single unified FastMCP endpoint on /mcp, with SessionMiddleware
        dynamically resolving scenario/session context from incoming headers (X-Session-ID, X-MCP-Scenario).
        """
        # Create master FastMCP instance containing all mock capabilities
        master_instance = create_mcp_server(
            name="Mock MCP Server",
            description="Unified Mock MCP Server with dynamic scenario context resolution via headers",
            tools=["*"],
            resources=["*"],
            prompts=["*"],
        )
        master_sub_app = master_instance.http_app(transport="streamable-http")

        default_sc_id = self.scenario_configs[0].id if self.scenario_configs else "default_scenario"
        header_session = self.yaml_config.gateway.header_session_name if self.yaml_config else "X-Session-ID"
        header_scenario = self.yaml_config.gateway.header_scenario_name if self.yaml_config else "X-MCP-Scenario"

        # Pass dictionary mapping scenario_id -> fixture_state
        master_sub_app.add_middleware(
            SessionMiddleware,
            header_session_name=header_session,
            header_scenario_name=header_scenario,
            default_scenario_id=default_sc_id,
            default_server_id="mock-mcp-server",
            initial_fixture_state=self.scenario_fixture_states,
        )

        @contextlib.asynccontextmanager
        async def mock_lifespan(app_instance: Starlette):
            async with contextlib.AsyncExitStack() as stack:
                if hasattr(master_sub_app, "lifespan") and callable(master_sub_app.lifespan):
                    await stack.enter_async_context(master_sub_app.lifespan(master_sub_app))

                import os
                import asyncio
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

        from starlette.responses import JSONResponse
        from starlette.routing import Route

        async def homepage(request):
            return JSONResponse({
                "service": "mock_mcp",
                "status": "online",
                "endpoint": "/mcp",
                "scenarios": [sc.id for sc in self.scenario_configs],
                "note": "Pass X-Session-ID and X-MCP-Scenario headers to switch session/scenario context dynamically."
            })

        async def health(request):
            return JSONResponse({"status": "healthy"})

        routes = [
            Route("/", homepage),
            Route("/health", health),
        ]
        app = Starlette(routes=routes, lifespan=mock_lifespan)

        # Mount single unified endpoint at /mcp
        app.mount("/mcp", master_sub_app)

        # Mount scenario prefixes pointing to master_sub_app for backwards compatibility
        for sc in self.scenario_configs:
            if sc.enable_gateway and sc.prefix != "/mcp":
                app.mount(sc.prefix, master_sub_app)

        return app

    def run_gateway(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """Run all Scenarios under a single HTTP Gateway process."""
        target_host = host or self.gateway_host
        target_port = port or 2027

        logger.info(f"=== Starting Standalone Mock MCP Service on {target_host}:{target_port} ===")
        for sc in self.scenario_configs:
            if sc.enable_gateway:
                endpoint = f"{sc.prefix}/mcp"
                logger.info(f" -> Mock Scenario Endpoint [{sc.id}]: http://{target_host}:{target_port}{endpoint}")

        app = self.create_mock_app()
        uvicorn.run(app, host=target_host, port=target_port)
