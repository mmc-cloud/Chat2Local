"""Two-level discovery through registry, dispatcher and the public MCP contract."""
import json

import jsonschema
import pytest
from conftest import run
from mcp.server.mcpserver.exceptions import ToolError
from test_mcp_surface import payload

from chat2local.browser.bridge import BrowserBridge
from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.mcp.instructions import MCP_INSTRUCTIONS
from chat2local.mcp.output_schema import OUTPUT_SCHEMAS
from chat2local.mcp.server import create_mcp_server
from chat2local.runtime.config import AppConfig, BrowserConfig
from chat2local.runtime.workspace import WorkspaceManager


def test_summary_is_default_and_has_no_full_definitions_or_guidance():
    registry = CapabilityRegistry(BrowserBridge(BrowserConfig()))
    summary = registry.discover()
    full = registry.discover(detail="full")
    assert summary == registry.discover(detail="summary")
    assert set(summary["result"]) == {"capabilities"}
    assert len(summary["result"]["capabilities"]) == 24
    assert [item["name"] for item in summary["result"]["capabilities"]] == [item["name"] for item in full["result"]["capabilities"]]
    for brief, complete in zip(summary["result"]["capabilities"], full["result"]["capabilities"]):
        assert set(brief) == {"name", "description", "available"}
        assert brief == {key: complete[key] for key in brief}
        assert set(complete) == {*brief, "input_schema", "result_contract"}
        assert complete["input_schema"] and complete["result_contract"]
    assert full["result"]["conventions"] and full["result"]["error_guidance"]
    size = lambda result: len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert size(summary) < size(full) / 5  # Relative reduction, no fragile exact byte budget.
    for result in (summary, full):
        jsonschema.validate(result, OUTPUT_SCHEMAS["capability_discover"])


def test_summary_does_not_generate_schemas(monkeypatch):
    registry = CapabilityRegistry(BrowserBridge(BrowserConfig()))
    def unexpected_schema(*args, **kwargs):
        pytest.fail("Summary must not generate full schemas")
    for model in {entry.arguments for entry in registry._entries.values()}:
        monkeypatch.setattr(model, "model_json_schema", unexpected_schema)
    assert len(registry.discover()["result"]["capabilities"]) == 24


@pytest.mark.parametrize("detail", ["summary", "full"])
def test_search_and_no_matches_are_unchanged_in_both_modes(detail):
    registry = CapabilityRegistry(BrowserBridge(BrowserConfig()))
    filtered = registry.discover("BROWSER.PAGE.SCROLL", detail=detail)
    assert [item["name"] for item in filtered["result"]["capabilities"]] == ["browser.page.scroll"]
    assert registry.discover("  BROWSER.PAGE.SCROLL  ", detail=detail) == filtered
    words = registry.discover("  BrOwSeR   ScRoLl  ", detail=detail)["result"]["capabilities"]
    assert "browser.page.scroll" in {item["name"] for item in words}
    assert all(all(word in f"{item['name']} {item['description']}".casefold() for word in ("browser", "scroll")) for item in words)
    assert registry.discover("browser.page.unknown", detail=detail)["result"]["capabilities"] == []
    jsonschema.validate(registry.discover("browser.page.unknown", detail=detail), OUTPUT_SCHEMAS["capability_discover"])


@pytest.mark.parametrize("detail", [None, "", "FULL", "verbose", True, 1, [], {}])
def test_bad_detail_is_refused_at_registry_and_mcp(tmp_path, detail):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    assert local.capabilities.discover(detail=detail)["error"]["code"] == "invalid_arguments"
    server = create_mcp_server(DeviceRouter("local", local))
    with pytest.raises(ToolError):
        run(server.call_tool("capability_discover", {"detail": detail}))


def test_mcp_input_defaults_and_two_level_instructions(tmp_path):
    server = create_mcp_server(DeviceRouter("local", LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())))
    async def scenario():
        tool = next(tool for tool in await server.list_tools() if tool.name == "capability_discover")
        schema = tool.input_schema
        jsonschema.Draft202012Validator.check_schema(schema)
        assert schema["properties"]["detail"]["enum"] == ["summary", "full"]
        assert schema["properties"]["detail"]["default"] == "summary"
        assert "detail" not in schema.get("required", [])
        assert "Defaults to summary" in tool.description
        default = payload(await server.call_tool("capability_discover", {}))
        explicit = payload(await server.call_tool("capability_discover", {"detail": "summary"}))
        assert default == explicit
        full = payload(await server.call_tool("capability_discover", {"query": "browser.page.scroll", "detail": "full"}))
        assert len(full["result"]["capabilities"]) == 1
        assert full["result"]["capabilities"][0]["input_schema"]["properties"]["container"]
        for result in (default, full):
            jsonschema.validate(result, tool.output_schema)
    run(scenario())
    assert 'detail="summary"' in MCP_INSTRUCTIONS and 'detail="full"' in MCP_INSTRUCTIONS
    assert "summary is the default" in MCP_INSTRUCTIONS


def test_output_schema_rejects_partial_full_or_mixed_summary_shapes():
    registry = CapabilityRegistry(BrowserBridge(BrowserConfig()))
    full = registry.discover("browser.page.scroll", detail="full")
    partial = json.loads(json.dumps(full))
    del partial["result"]["conventions"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(partial, OUTPUT_SCHEMAS["capability_discover"])
    partial = registry.discover("browser.page.scroll")
    partial["result"]["error_guidance"] = {}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(partial, OUTPUT_SCHEMAS["capability_discover"])
