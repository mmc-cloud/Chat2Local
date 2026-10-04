"""One construction entry point, frozen tools and independent router binding."""

import ast
import importlib
import inspect

import pytest

from chat2local.app import create_app
from chat2local.hub.router import DeviceRouter
from chat2local.mcp.instructions import MCP_INSTRUCTIONS
from chat2local.mcp.server import create_mcp_server
from chat2local.runtime.config import AuthConfig
from conftest import run
from test_auth_config import OAUTH
from test_dispatch import make_dispatcher
from test_mcp_surface import payload

server_module = importlib.import_module("chat2local.mcp.server")


@pytest.mark.parametrize("auth", [None, AuthConfig(), AuthConfig(**OAUTH)])
def test_factory_registers_exactly_once_with_frozen_surface(tmp_path, monkeypatch, auth):
    registered = []
    constructed = []
    original_register = server_module.register_tools
    original_server = server_module.MCPServer

    def register(server, router):
        registered.append((server, router))
        original_register(server, router)

    def construct(*args, **kwargs):
        constructed.append(kwargs)
        return original_server(*args, **kwargs)

    monkeypatch.setattr(server_module, "register_tools", register)
    monkeypatch.setattr(server_module, "MCPServer", construct)
    router = DeviceRouter("desktop", make_dispatcher(tmp_path))
    server = create_mcp_server(router, auth=auth)
    assert registered == [(server, router)]
    assert len(constructed) == 1
    tools = run(server.list_tools())
    assert len(tools) == 10
    assert {tool.name for tool in tools} == {
        "read", "search", "list_devices", "apply_patch", "exec_command", "interact_process",
        "kill_process", "handoff_list", "handoff_get", "handoff_save",
    }
    assert len(router.local.tools) == 9
    assert server.instructions == MCP_INSTRUCTIONS
    if auth is None or auth.mode == "none":
        assert "auth" not in constructed[0] and "token_verifier" not in constructed[0]
    else:
        from chat2local.auth.workos import WorkOSTokenVerifier
        assert isinstance(constructed[0]["token_verifier"], WorkOSTokenVerifier)
        settings = constructed[0]["auth"]
        assert str(settings.issuer_url) == OAUTH["issuer_url"]
        assert str(settings.resource_server_url) == OAUTH["resource_server_url"]
        assert settings.validate_token_resource is True and settings.required_scopes == []
    assert payload(run(server.call_tool("list_devices", {})))["devices"][0]["device_id"] == "desktop"


def test_factory_instances_use_their_own_router(tmp_path):
    first = create_mcp_server(DeviceRouter("first", make_dispatcher(tmp_path)))
    second = create_mcp_server(DeviceRouter("second", make_dispatcher(tmp_path)))
    for server, name in [(first, "first"), (second, "second")]:
        assert payload(run(server.call_tool("list_devices", {})))["devices"][0]["device_id"] == name


def test_app_delegates_construction_without_second_registration(tmp_path, monkeypatch):
    app_module = importlib.import_module("chat2local.app")
    calls = []
    original = app_module.create_mcp_server

    def factory(router, *, auth):
        calls.append((router, auth))
        return original(router, auth=auth)

    monkeypatch.setattr(app_module, "create_mcp_server", factory)
    router = DeviceRouter("local", make_dispatcher(tmp_path))
    auth = AuthConfig(**OAUTH)
    application = create_app(router, auth=auth)
    assert calls == [(router, auth)]
    assert len(run(application.state.mcp.list_tools())) == 10
    tree = ast.parse(inspect.getsource(app_module))
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in ("MCPServer", "register", "register_tools") for node in ast.walk(tree))
