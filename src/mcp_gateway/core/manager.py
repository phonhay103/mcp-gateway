"""
Enterprise MCP Gateway Manager.
Orchestrates Gateway startup, applies Enterprise Middlewares, and mounts Control Plane & Data Plane endpoints.
"""

from pathlib import Path
from typing import Optional
import contextlib
import yaml
from starlette.applications import Starlette
import uvicorn

from mcp_gateway.core.adapters import adapter_registry
from mcp_gateway.api.api_endpoints import create_gateway_routes
from mcp_gateway.security.auth import AuthConfig, AuthMiddleware
from mcp_gateway.core.config import GatewayYamlConfig
from mcp_gateway.logger import logger, setup_logging
from mcp_gateway.security.rate_limiter import RateLimitMiddleware
from mcp_gateway.security.security import EnterpriseSecurityMiddleware


class ServerManager:
    """
    Manager loading Gateway config via Pydantic v2 and orchestrating the Gateway endpoints.
    """

    def __init__(self, config_path: str | Path):
        self.config_path = Path(config_path)
        self.yaml_config: Optional[GatewayYamlConfig] = None

        setup_logging()
        self.load_config()

    @property
    def gateway_host(self) -> str:
        return self.yaml_config.gateway.host if self.yaml_config else "0.0.0.0"

    @property
    def gateway_port(self) -> int:
        return self.yaml_config.gateway.port if self.yaml_config else 2026

    @property
    def rate_limit_enabled(self) -> bool:
        return self.yaml_config.gateway.rate_limiting.enabled if self.yaml_config else True

    @property
    def rate_limit_rpm(self) -> int:
        return self.yaml_config.gateway.rate_limiting.requests_per_minute if self.yaml_config else 120

    @property
    def auth_config(self) -> AuthConfig:
        return self.yaml_config.gateway.auth if self.yaml_config else AuthConfig()

    def load_config(self) -> None:
        """Read main configuration YAML and register upstream adapters."""
        if not self.config_path.exists():
            logger.error(f"Configuration file not found: {self.config_path}")
            raise FileNotFoundError(f"Configuration file not found: {self.config_path}")

        with open(self.config_path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f) or {}

        try:
            self.yaml_config = GatewayYamlConfig.model_validate(raw_data)
            logger.info("Enterprise Gateway configuration loaded successfully via Pydantic v2.")
        except Exception as e:
            logger.error(f"Failed to validate Gateway YAML configuration: {e}")
            raise e
            
        # Register adapters from config
        if self.yaml_config.adapters:
            for adapter_cfg in self.yaml_config.adapters:
                adapter_registry.register_adapter(adapter_cfg)

    def _apply_middleware_pipeline(
        self, app: Starlette, allowed_origins: list[str]
    ) -> Starlette:
        """Apply Enterprise Middlewares in reverse order (outermost first)."""

        # 1. Base Security (CORS, HSTS, X-Frame-Options)
        app.add_middleware(
            EnterpriseSecurityMiddleware,
            allowed_origins=allowed_origins,
        )

        # 2. Advanced API Key / JWT Auth
        if self.auth_config.enabled:
            app.add_middleware(AuthMiddleware, auth_config=self.auth_config)

        # 3. Rate Limiting
        if self.rate_limit_enabled:
            app.add_middleware(RateLimitMiddleware, requests_per_minute=self.rate_limit_rpm)

        return app

    def create_gateway_app(self) -> Starlette:
        """Create the Unified Gateway Starlette application."""
        gateway_routes = create_gateway_routes()

        @contextlib.asynccontextmanager
        async def gateway_lifespan(app_instance: Starlette):
            logger.info(f"Enterprise Gateway Unified Runtime started. Plugged-in Adapters: {len(adapter_registry.list_adapters())}")
            yield
            logger.info("Enterprise Gateway Unified Runtime shutting down.")

        app = Starlette(
            routes=gateway_routes,
            lifespan=gateway_lifespan,
        )

        allowed_origins = self.yaml_config.gateway.allowed_origins if self.yaml_config else ["*"]
        app = self._apply_middleware_pipeline(app, allowed_origins)
        
        return app

    def run_gateway(self, host: Optional[str] = None, port: Optional[int] = None) -> None:
        """Run the Shared Gateway application."""
        host = host or self.gateway_host
        port = port or self.gateway_port

        logger.info(f"Starting Enterprise MCP Gateway on {host}:{port}...")

        app = self.create_gateway_app()

        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
            access_log=False,
            timeout_keep_alive=60,
        )
