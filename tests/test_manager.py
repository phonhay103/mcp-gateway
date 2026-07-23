"""
Tests for Multi-MCP Server Manager and Registry.
"""

from pathlib import Path
import pytest
import yaml

from mock_mcp_gateway.manager import ServerManager
from mock_mcp_gateway.registry import register_features
from mock_mcp_gateway.server import create_mcp_server


def test_create_custom_mcp_server():
    server = create_mcp_server(
        name="Custom Server",
        description="Test description",
        tools=["echo"],
        resources=["system://status"],
        prompts=[],
    )
    assert server.name == "Custom Server"


def test_server_manager_load_yaml(tmp_path: Path):
    yaml_content = {
        "gateway": {"host": "127.0.0.1", "port": 9000, "enabled": True},
        "servers": [
            {
                "name": "srv1",
                "description": "Server 1",
                "transport": "streamable-http",
                "host": "127.0.0.1",
                "port": 9001,
                "prefix": "/srv1",
                "enable_gateway": True,
                "enable_standalone": True,
                "tools": ["echo"],
                "resources": [],
                "prompts": [],
            },
            {
                "name": "srv2",
                "description": "Server 2",
                "transport": "streamable-http",
                "host": "127.0.0.1",
                "port": 9002,
                "prefix": "/srv2",
                "enable_gateway": True,
                "enable_standalone": False,
                "tools": ["calculate"],
                "resources": [],
                "prompts": [],
            },
        ],
    }

    config_file = tmp_path / "servers.yaml"
    with open(config_file, "w") as f:
        yaml.dump(yaml_content, f)

    manager = ServerManager(config_file)
    assert manager.gateway_port == 9000
    assert len(manager.server_configs) == 2
    assert manager.server_configs[0].enable_standalone is True
    assert manager.server_configs[1].enable_standalone is False
    assert "srv1" in manager.instances
    assert "srv2" in manager.instances


def test_server_manager_gateway_app(tmp_path: Path):
    yaml_content = {
        "gateway": {"host": "127.0.0.1", "port": 8000},
        "servers": [
            {
                "name": "srv1",
                "description": "Server 1",
                "transport": "streamable-http",
                "host": "127.0.0.1",
                "port": 8001,
                "prefix": "/srv1",
                "tools": ["echo"],
            }
        ],
    }

    config_file = tmp_path / "servers.yaml"
    with open(config_file, "w") as f:
        yaml.dump(yaml_content, f)

    manager = ServerManager(config_file)
    app = manager.create_gateway_app()
    assert app is not None
