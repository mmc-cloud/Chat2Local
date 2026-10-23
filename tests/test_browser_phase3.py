"""Phase 3 validation, safe error transport and interrupted action recovery."""
import asyncio
import json

import pytest

from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.browser.errors import extension_failure
from conftest import run
from test_browser_bridge import browser_config, running_bridge, extension, response


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
