"""Multi-capability contracts, per-operation availability and bounded errors."""
import asyncio
import json

import pytest

from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.browser.errors import CAPABILITY_NAMES, extension_failure
from chat2local.mcp.server import create_mcp_server
from chat2local.mcp.output_schema import OUTPUT_SCHEMAS
from conftest import run
from test_browser_bridge import browser_config, running_bridge, extension, response


def test_independent_availability_and_mixed_out_of_order_responses(tmp_path):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            assert {entry['name'] for entry in registry.discover()['result']['capabilities']} == set(CAPABILITY_NAMES)
            supported = ["browser.tabs.list", "browser.page.click"]
            async with extension(bridge, capabilities=supported) as ws:
                entries = registry.discover()['result']['capabilities']
                assert all(entry['available'] is (entry['name'] in supported) for entry in entries)
                assert not bridge.available and bridge.is_available("browser.page.click")
                refused = await registry.execute("browser.page.fill", {"tab": {"tab_id": 3}, "target": {"name": "Query"}, "value": "text"})
                assert refused['error']['code'] == 'capability_unavailable' and not bridge._pending
                first = asyncio.create_task(registry.execute("browser.tabs.list", {}))
                second = asyncio.create_task(registry.execute("browser.page.click", {"tab": {"tab_id": 3}, "target": {"role": "button", "name": "Go"}}))
                a, b = [json.loads(await ws.recv()) for _ in range(2)]
                assert a['capability'] == 'browser.tabs.list' and b['capability'] == 'browser.page.click'
                assert b['arguments']['target'] == {"role": "button", "name": "Go"}
                await ws.send(response(b, result={"status": "clicked"}))
                await ws.send(response(a, result={"tabs": []}))
                assert (await first)['result'] == {"tabs": []}
                assert (await second)['result'] == {"status": "clicked"}
            assert all(not entry['available'] for entry in registry.discover()['result']['capabilities'])
            async with extension(bridge, capabilities=["browser.tabs.create"]):
                assert bridge.available and not bridge.is_available("browser.page.click")
    run(scenario())


@pytest.mark.parametrize("name,args", [
    ("browser.tabs.resolve", {}), ("browser.tabs.resolve", {"tab": {}}),
    ("browser.tabs.resolve", {"tab": {"tab_id": True}}),
    ("browser.tabs.resolve", {"tab": {"active": True}}),
    ("browser.tabs.list", {"limit": 51}), ("browser.tabs.list", {"limit": 1.5}),
    ("browser.page.inspect", {"tab": {"tab_id": 1}, "target": {}}),
    ("browser.page.inspect", {"tab": {"tab_id": 1}, "limit": 21}),
    ("browser.page.click", {"tab": {"url": "https://example.com"}}),
    ("browser.page.click", {"tab": {"tab_id": 1}, "target": {"selector": "button"}}),
    ("browser.page.click", {"tab": {"tab_id": 1}, "target": {"name": "Go"}, "wait_ms": 5001}),
    ("browser.page.fill", {"tab": {"tab_id": 1}, "target": {"name": "Query"}, "value": 1}),
    ("browser.page.fill", {"tab": {"tab_id": 1}, "target": {"name": "Query"}, "value": "a" * 8193}),
    ("browser.page.click", {"tab": {"tab_id": 1}, "target": {"css": "x" * 1025}}),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}}),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "scope": "#dialog"}),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "css": ""}),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "css": "div", "depth": 4}),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "css": "div", "limit": 11}),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "tag": "div > span"}),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "css": "div", "javascript": "alert(1)"}),
    ("browser.page.read", {}),
    ("browser.page.read", {"tab": {"tab_id": 1}, "max_chars": 999}),
    ("browser.page.read", {"tab": {"tab_id": 1}, "max_chars": 12001}),
    ("browser.page.read", {"tab": {"tab_id": 1}, "max_chars": True}),
    ("browser.page.read", {"tab": {"tab_id": 1}, "cursor": ""}),
    ("browser.page.read", {"tab": {"tab_id": 1}, "cursor": "a" * 513}),
    ("browser.page.read", {"tab": {"tab_id": 1}, "cursor": "!"}),
    ("browser.page.read", {"tab": {"tab_id": 1}, "javascript": "alert(1)"}),
])
def test_strict_per_capability_schemas(tmp_path, name, args):
    from chat2local.browser.bridge import BrowserBridge
    registry = CapabilityRegistry(BrowserBridge(browser_config(tmp_path)))
    assert run(registry.execute(name, args))['error']['code'] == 'invalid_arguments'


def test_bounded_sanitized_error_candidates_match_public_schema():
    jsonschema = pytest.importorskip('jsonschema')
    result = extension_failure({"code": "ambiguous_tab", "message": "SECRET", "details": {"candidates": [
        {"tab_id": 3, "window_id": True, "title": "a" * 300, "url": "https://example.com", "secret": "SECRET"}
    ] * 10}})
    assert len(result['error']['details']['candidates']) == 5
    assert len(result['error']['details']['candidates'][0]['title']) == 200
    assert 'window_id' not in result['error']['details']['candidates'][0]
    assert 'SECRET' not in json.dumps(result)
    jsonschema.validate(result, OUTPUT_SCHEMAS['capability_execute'])
    for code in ('permission_denied', 'page_changed', 'protected_input', 'stale_target',
                 'invalid_selector', 'scope_not_found', 'ambiguous_scope', 'invalid_cursor'):
        assert extension_failure({"code": code, "message": "SECRET"})['error']['code'] == code
    assert extension_failure({"code": ["bad"]})['error']['code'] == 'browser_operation_failed'


def test_public_surface_remains_twelve_and_capability_schemas_valid(tmp_path):
    from chat2local.browser.bridge import BrowserBridge
    jsonschema = pytest.importorskip('jsonschema')
    assert len(run(create_mcp_server().list_tools())) == 12
    for entry in CapabilityRegistry(BrowserBridge(browser_config(tmp_path))).discover(detail="full")['result']['capabilities']:
        jsonschema.Draft202012Validator.check_schema(entry['input_schema'])
