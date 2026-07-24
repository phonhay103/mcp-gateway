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

from mock_mcp_gateway.core.adapters import AdapterStatus, AdapterType, PlugInAdapterConfig, adapter_registry
from mock_mcp_gateway.api.api_endpoints import create_gateway_routes
from mock_mcp_gateway.security.auth import AuthConfig, AuthMiddleware
from mock_mcp_gateway.core.config import (
    BaseMCPServerConfig,
    ErrorSimulationRule,
    GatewayConfig,
    GatewayYamlConfig,
    RateLimitConfig,
    ScenarioConfig,
    ServerConfig,
    ToolBehaviorConfig,
    ToolErrorConfig,
)
from mock_mcp_gateway.core.fixtures import FixtureLoader
from mock_mcp_gateway.observability.logger import logger, setup_logging
from mock_mcp_gateway.security.rate_limiter import RateLimitMiddleware
from mock_mcp_gateway.core.schemas import schema_registry
from mock_mcp_gateway.security.security import EnterpriseSecurityMiddleware
from mock_mcp_gateway.core.session import SessionMiddleware, session_manager
from mock_mcp_gateway.core.server import create_mcp_server


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

    def _apply_middleware_pipeline(
        self,
        app: Starlette,
        scenario_id: str = "default_scenario",
        server_prefix_map: Optional[Dict[str, str]] = None,
    ) -> None:
        """Apply Enterprise Middlewares (Session -> Auth -> Rate Limiting -> OWASP Security Headers)."""
        allowed_origins = self.yaml_config.gateway.allowed_origins if self.yaml_config else ["*"]
        app.add_middleware(EnterpriseSecurityMiddleware, allowed_origins=allowed_origins)
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

        mounted_sub_apps: Dict[str, Starlette] = {}
        for sc in gateway_scenarios:
            instance = self.instances[sc.id]
            mounted_sub_apps[sc.id] = instance.http_app(transport=sc.transport)

        @contextlib.asynccontextmanager
        async def gateway_lifespan(app_instance: Starlette):
            async with contextlib.AsyncExitStack() as stack:
                for sc_id, sub_app in mounted_sub_apps.items():
                    if hasattr(sub_app, "lifespan") and callable(sub_app.lifespan):
                        await stack.enter_async_context(sub_app.lifespan(sub_app))
                yield

        routes = create_gateway_routes(self)
        app = Starlette(routes=routes, lifespan=gateway_lifespan)

        for sc in gateway_scenarios:
            sub_app = mounted_sub_apps[sc.id]

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

        self._apply_middleware_pipeline(app, scenario_id="gateway_index", server_prefix_map=prefix_map)
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
