"""
Tests for Pydantic v2 Configuration Validation, Data Models, and Exact Tool Whitelisting in Scenarios.
"""

from pathlib import Path
import pytest
import yaml
from pydantic import ValidationError

from mock_mcp_gateway.auth import APIKeyInfo, AuthConfig
from mock_mcp_gateway.manager import GatewayConfig, GatewayYamlConfig, ScenarioConfig, BaseMCPServerConfig, ServerManager
from mock_mcp_gateway.tools import EchoOutput, SystemInfoModel, UserRecord


def test_pydantic_scenario_config():
    data = {
        "id": "sysops-scenario",
        "name": "SysOps Operations",
        "description": "System Operations Scenario",
        "port": 8001,
        "tools": ["echo", "get_system_info"],
    }
    sc = ScenarioConfig.model_validate(data)
    assert sc.id == "sysops-scenario"
    assert sc.port == 8001
    assert sc.prefix == "/sysops-scenario"
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
                "port": 8002,
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


def test_exact_scenario_tools_whitelisting(tmp_path: Path):
    """Verify that a scenario specifying explicit 'tools' loads ONLY those exact tools."""
    yaml_content = {
        "gateway": {"host": "127.0.0.1", "port": 8000},
        "servers": [
            {
                "id": "full-utility-server",
                "tools": ["calculate", "manage_users", "generate_random_data"],
            }
        ],
        "scenarios": [
            {
                "id": "calculator-only",
                "servers": ["full-utility-server"],
                "tools": ["calculate"],  # Whitelist ONLY 'calculate'
                "prefix": "/calc",
            }
        ],
    }

    config_file = tmp_path / "custom_scenario.yaml"
    with open(config_file, "w") as f:
        yaml.dump(yaml_content, f)

    manager = ServerManager(config_file)
    scenario_cfg = manager.scenario_configs[0]
    loaded_tools, _, _ = manager._resolve_scenario_capabilities(scenario_cfg)

    # Must contain ONLY calculate, not manage_users or generate_random_data
    assert loaded_tools == ["calculate"]


def test_pydantic_validation_error_on_missing_required_fields():
    # UserRecord requires 'id', 'name', 'email'
    invalid_data = {"id": "user_1", "role": "admin"}
    with pytest.raises(ValidationError):
        UserRecord.model_validate(invalid_data)


def test_pydantic_tools_models():
    user = UserRecord(id="u123", name="Jane Doe", email="jane@example.com", role="admin")
    assert user.id == "u123"
    assert user.email == "jane@example.com"

    dump = user.model_dump()
    assert dump["role"] == "admin"
