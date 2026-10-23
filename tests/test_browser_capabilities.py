"""Capability contracts do not depend on Chrome or an enabled listener."""

import pytest
from pydantic import ValidationError

from chat2local.browser.bridge import CAPABILITY
from chat2local.browser.capabilities import CapabilityRegistry, CreateTabArguments
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.mcp.server import create_mcp_server
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import AppConfig, BrowserConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run
from test_mcp_surface import payload


@pytest.mark.parametrize("arguments", [
    {}, {"url": None}, {"url": 1}, {"url": True}, {"url": []},
    {"url": "https://example.com", "active": False},
    *[{"url": url} for url in (
        "", "example.com", "javascript:alert(1)", "file:///tmp/a", "chrome://settings",
        "data:text/html,x", "https://", "https:///example.com", "https://[invalid",
        "http://example.com:65536", "http://example.com:0", "https://user:password@example.com",
        "https://example.com/a b", "https://example.com/\n", "https://example.com/\x00",
        "https://example.com/\u0085", "https://example.com/\\x", "https://example.com/" + "a" * 8192,
    )],
])
def test_invalid_capability_arguments_rejected_even_when_disconnected(tmp_path, arguments):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    result = run(local.execute("capability_execute", {"capability": CAPABILITY, "arguments": arguments}))
    assert result["ok"] is False and result["error"]["code"] == "invalid_arguments"
    assert local.browser.server is None


@pytest.mark.parametrize("url", [
    "https://example.com", "http://localhost:8080/a?q=one#fragment",
    "https://例子.测试/你好", "http://127.0.0.1/", "https://[::1]:443/",
])
def test_valid_http_urls_preserved(url):
    assert CreateTabArguments.model_validate({"url": url}).model_dump() == {"url": url}


def test_discovery_and_unknown_unavailable_are_distinct(tmp_path):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    async def scenario():
        for query in (CAPABILITY, "browser create"):
            result = await local.execute("capability_discover", {"query": query})
            entries = result["result"]["capabilities"]
            assert len(entries) == 1
            assert entries[0]["name"] == CAPABILITY and entries[0]["available"] is False
            schema = entries[0]["input_schema"]
            assert schema["required"] == ["url"] and schema["additionalProperties"] is False
        assert (await local.execute("capability_discover", {"query": "browser.page.unknown"}))["result"]["capabilities"] == []
        unknown = await local.execute("capability_execute", {"capability": "unknown", "arguments": {}})
        assert unknown["error"]["code"] == "unknown_capability"
        unavailable = await local.execute("capability_execute", {"capability": CAPABILITY, "arguments": {"url": "https://example.com"}})
        assert unavailable["error"]["code"] == "capability_unavailable"
        assert local.browser.server is None
    run(scenario())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("tool,args", [
    ("capability_discover", {"query": 1}), ("capability_discover", {"query": "x" * 257}),
    ("capability_discover", {"extra": True}), ("capability_execute", {}),
    ("capability_execute", {"capability": CAPABILITY}),
    ("capability_execute", {"capability": CAPABILITY, "arguments": []}),
    ("capability_execute", {"capability": CAPABILITY, "arguments": {}, "device": "other"}),
])
def test_outer_dispatch_validation_uses_failure_envelope(tmp_path, tool, args):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    result = run(local.execute(tool, args))
    assert result["ok"] is False and result["error"]["code"] == "invalid_arguments"


def test_registry_rejects_duplicate_names(tmp_path):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    registry = CapabilityRegistry(local.browser)
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(registry._entries[CAPABILITY])


def test_mcp_public_schemas_and_failure_envelopes(tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    server = create_mcp_server(DeviceRouter("local", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())))
    async def scenario():
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert len(tools) == 12
        assert set(tools["capability_discover"].input_schema["properties"]) == {"query", "device"}
        assert set(tools["capability_execute"].input_schema["properties"]) == {"capability", "arguments", "device"}
        assert tools["capability_execute"].input_schema["required"] == ["capability", "arguments"]
        for tool, args in (
            ("capability_discover", {}), ("capability_discover", {"device": "offline"}),
            ("capability_execute", {"capability": "unknown", "arguments": {}}),
            ("capability_execute", {"capability": CAPABILITY, "arguments": {}}),
            ("capability_execute", {"capability": CAPABILITY, "arguments": {"url": "https://example.com"}}),
            ("capability_execute", {"capability": CAPABILITY, "arguments": {}, "device": "offline"}),
        ):
            result = payload(await server.call_tool(tool, args))
            jsonschema.validate(result, tools[tool].output_schema)
            if args.get("device") == "offline":
                assert result["error"]["code"] == "device_unavailable"
    run(scenario())


@pytest.mark.parametrize("options", [
    {"enabled": True}, {"enabled": True, "extension_id": "a" * 32},
    {"port": 0}, {"port": 65536}, {"port": True}, {"enabled": "true"},
    {"extension_id": "https://example.com"}, {"extension_id": "z" * 32},
    {"token_file": ""}, {"host": "0.0.0.0"},
])
def test_browser_configuration_is_strict(options):
    with pytest.raises(ValidationError):
        BrowserConfig(**options)
