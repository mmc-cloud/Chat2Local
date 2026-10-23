"""Phase 3 validation, safe error transport and interrupted action recovery."""
import asyncio
import json

import pytest

from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.browser.errors import extension_failure
from conftest import run
from test_browser_bridge import browser_config, running_bridge, extension, response


@pytest.mark.parametrize("options", [
    {"offset": -1}, {"offset": 10000}, {"limit": 0}, {"limit": 51},
    {"limit": True}, {"offset": "0"}, {"limit": 1.5}, {"unexpected": True}, [],
])
def test_native_option_page_arguments_are_rejected_before_transport(tmp_path, options):
    from chat2local.browser.bridge import BrowserBridge
    registry = CapabilityRegistry(BrowserBridge(browser_config(tmp_path)))
    result = run(registry.execute("browser.page.inspect", {
        "tab": {"tab_id": 1}, "target": {"id": "choices"}, "select_options": options}))
    assert result["error"]["code"] == "invalid_arguments"


def test_option_discovery_defaults_and_control_observations_cross_actual_bridge_once(tmp_path):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=["browser.page.inspect"]) as ws:
                pending = asyncio.create_task(registry.execute("browser.page.inspect", {
                    "tab": {"tab_id": 1}, "target": {"ref": "el_select"}, "select_options": {"offset": 20}}))
                request = json.loads(await ws.recv())
                assert request["arguments"]["select_options"] == {"offset": 20, "limit": 20}
                assert request["arguments"]["target"] == {"ref": "el_select"}
                result = {"elements": [{"ref": "el_select", "state": {"selection": {
                    "multiple": False, "selected_count": 1, "selected_indices": [21], "total_options": 22, "has_more": False}},
                    "select_options": {"options": [{"index": 21, "label": "Last", "value": "last", "selected": True, "disabled": False}],
                                       "total": 22, "offset": 20, "has_more": False, "next_offset": None, "truncated": False},
                    "scroll": {"y": {"scrollable": False, "at_start": True, "at_end": True}}}], "total": 1, "truncated": False}
                await ws.send(response(request, result=result))
                assert (await pending)["result"] == result
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.05):
                        await ws.recv()
    run(scenario())


@pytest.mark.parametrize("operation,extra", [
    ("select", {"options": [{"value": None, "index": None}]}),
    ("select", {"options": [{"index": True}]}),
    ("select", {"options": [{"index": -1}]}),
    ("set_checked", {"checked": 1}),
    ("set_checked", {"checked": "true"}),
    ("press_key", {"key": "Control+a"}),
    ("press_key", {"key": "Enter", "modifiers": ["Ctrl"]}),
    ("press_key", {"key": "Enter", "modifiers": ["Alt", "Alt"]}),
    ("wait_for", {"state": "gone"}),
    ("wait_for", {"timeout_ms": 10001}),
    ("wait_for", {"timeout_ms": True}),
    ("hover", {"javascript": "ignored"}),
    ("double_click", {"wait_ms": 5001}),
    ("focus", {"force": True}),
])
def test_invalid_phase3_arguments_are_refused_before_transport(tmp_path, operation, extra):
    from chat2local.browser.bridge import BrowserBridge
    registry = CapabilityRegistry(BrowserBridge(browser_config(tmp_path)))
    result = run(registry.execute(f"browser.page.{operation}", {"tab": {"tab_id": 1}, "target": {"id": "x"}, **extra}))
    assert result["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("code", ["unsupported_state", "state_not_reached", "option_not_found", "ambiguous_option",
                                   "option_disabled", "target_not_scrollable", "wait_timeout"])
def test_phase3_errors_keep_only_the_shared_safe_contract(code):
    result = extension_failure({"code": code, "message": "PRIVATE-VALUE", "details": {"value": "PRIVATE-VALUE"}})
    assert result["error"]["code"] == code
    assert "PRIVATE-VALUE" not in json.dumps(result)


@pytest.mark.parametrize("dispatched", [
    {"keydown": False, "keyup": False}, {"keydown": True, "keyup": False},
    {"keydown": True, "keyup": True},
])
def test_keyboard_partial_dispatch_details_are_bounded_and_survive_transport(dispatched):
    import jsonschema
    from chat2local.mcp.output_schema import capability_schema
    result = extension_failure({"code": "permission_denied", "details": {
        "events_dispatched": dispatched, "target": "PRIVATE-VALUE", "key": "PRIVATE-VALUE"}})
    assert result["error"]["details"] == {"events_dispatched": dispatched}
    assert "PRIVATE-VALUE" not in json.dumps(result)
    jsonschema.validate(result, capability_schema({"type": "object"}))


@pytest.mark.parametrize("dispatched", [
    None, [], {"keydown": True}, {"keydown": 1, "keyup": False},
    {"keydown": False, "keyup": True}, {"keydown": True, "keyup": False, "private": "PRIVATE-VALUE"},
])
def test_malformed_keyboard_progress_is_not_forwarded(dispatched):
    import jsonschema
    from chat2local.mcp.output_schema import capability_schema
    result = extension_failure({"code": "request_expired", "details": {"events_dispatched": dispatched}})
    assert "details" not in result["error"]
    assert not jsonschema.Draft202012Validator(capability_schema({"type": "object"})).is_valid({
        "ok": False, "error": {"code": "request_expired", "message": "safe", "details": {"events_dispatched": dispatched}}})


def test_keyboard_contract_explains_release_target_readonly_and_partial_completion(tmp_path):
    from chat2local.browser.bridge import BrowserBridge
    registry = CapabilityRegistry(BrowserBridge(browser_config(tmp_path)))
    full = registry.discover("browser.page.press_key", detail="full")["result"]
    capability = full["capabilities"][0]
    assert "Readonly allowed" in capability["description"]
    assert "keyup_target" in capability["result_contract"]
    assert "events_dispatched" in full["conventions"]["keyboard_completion"]
    assert "Unrelated DOM" in full["conventions"]["references"]


@pytest.mark.parametrize("keyup", [False, True])
def test_keyboard_dispatch_progress_survives_actual_bridge_without_replay(tmp_path, keyup):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=["browser.page.press_key"]) as ws:
                pending = asyncio.create_task(registry.execute("browser.page.press_key", {
                    "tab": {"tab_id": 1}, "target": {"ref": "el_current"}, "key": "Escape"}))
                request = json.loads(await ws.recv())
                progress = {"keydown": True, "keyup": keyup}
                await ws.send(response(request, error={"code": "permission_denied", "message": "PRIVATE",
                    "details": {"events_dispatched": progress, "private": "PRIVATE"}}))
                result = await pending
                assert result["error"]["details"] == {"events_dispatched": progress}
                assert "PRIVATE" not in json.dumps(result)
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.05):
                        await ws.recv()
    run(scenario())


def test_disconnected_phase3_action_is_never_replayed_after_reconnect(tmp_path):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            supported = ["browser.page.set_checked", "browser.page.wait_for"]
            arguments = {"tab": {"tab_id": 1}, "target": {"ref": "el_current"}, "checked": True}
            async with extension(bridge, capabilities=supported) as ws:
                pending = asyncio.create_task(registry.execute(supported[0], arguments))
                request = json.loads(await ws.recv())
                assert request["arguments"]["checked"] is True
                await ws.close()
                assert (await pending)["error"]["code"] == "capability_unavailable"
                assert not bridge._pending
            async with extension(bridge, capabilities=supported) as ws:
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.05):
                        await ws.recv()
                bridge.request_timeout = 0.05
                pending = asyncio.create_task(registry.execute(supported[0], arguments))
                await ws.recv()
                assert (await pending)["error"]["code"] == "capability_timeout"
                assert not bridge._pending
                # Late action response cannot satisfy a fresh read-only wait.
                await ws.send(response(request, result={"status": "checked"}))
                bridge.request_timeout = 1
                pending = asyncio.create_task(registry.execute(supported[1], {"tab": {"tab_id": 1}, "target": {"id": "x"}}))
                wait = json.loads(await ws.recv())
                assert wait["capability"] == supported[1] and wait["arguments"]["timeout_ms"] == 5000
                await ws.send(response(wait, error={"code": "wait_timeout", "message": "PRIVATE"}))
                assert (await pending)["error"]["code"] == "wait_timeout"
    run(scenario())
