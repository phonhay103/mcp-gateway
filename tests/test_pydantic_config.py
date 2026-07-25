"""
Tests for Pydantic v2 Configuration Validation, Data Models, and Exact Tool Whitelisting in Scenarios.
"""

from pathlib import Path
import pytest
import yaml
from pydantic import ValidationError

from mcp_gateway.auth import APIKeyInfo, AuthConfig
from mcp_gateway.manager import GatewayConfig, GatewayYamlConfig, ScenarioConfig, BaseMCPServerConfig, ServerManager
from mcp_gateway.tools import EchoOutput, SystemInfoModel, UserRecord


def test_pydantic_scenario_config():
    data = {
        "id": "sysops-scenario",
        "name": "SysOps Operations",
        "description": "System Operations Scenario",
        "prefix": "/sysops",
        "tools": ["echo", "get_system_info"],
    }
    sc = ScenarioConfig.model_validate(data)
    assert sc.id == "sysops-scenario"
    assert sc.prefix == "/sysops"
    assert sc.tools == ["echo", "get_system_info"]


def test_pydantic_gateway_yaml_config():
    yaml_data = {
        "version": "3.0",
        "gateway": {
            "host": "0.0.0.0",
            "port": 8000,
            "rate_limiting": {"enabled": True, "requests_per_minute": 200},
            "auth": {
                "enabled": True,
                "type": "api_key",
                "keys": [
                    {"name": "admin", "key": "admin-key", "allowed_servers": ["*"]}
                ],
            },
        },
        "servers": [
            {
                "id": "base-utility-server",
                "name": "Base Utility Server",
                "tools": ["calculate", "manage_users", "generate_random_data"],
            }
        ],
        "scenarios": [
            {
                "id": "utility-scenario",
                "servers": ["base-utility-server"],
                "prefix": "/utility",
            }
        ],
    }

    config = GatewayYamlConfig.model_validate(yaml_data)
    assert config.gateway.port == 8000
    assert config.gateway.rate_limiting.requests_per_minute == 200
    assert config.gateway.auth.enabled is True
    assert "admin-key" in config.gateway.auth.keys
    assert config.servers[0].id == "base-utility-server"
    assert config.scenarios[0].prefix == "/utility"
