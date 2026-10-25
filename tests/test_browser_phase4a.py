"""Phase 4A action contracts and side effects over the existing WebSocket bridge."""
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
from conftest import run
from pydantic import ValidationError
from test_browser_bridge import browser_config, extension, response, running_bridge

from chat2local.browser.bridge import BrowserBridge
from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.browser.errors import extension_failure
from chat2local.runtime.config import BrowserConfig

TAB = {"tab_id": 1}
OPERATIONS = [
    ("browser.tabs.manage", {"action": "activate", "tab": TAB}),
    ("browser.tabs.manage", {"action": "close", "tab": TAB}),
    ("browser.tabs.manage", {"action": "move", "tab": TAB, "index": -1, "window_id": 0}),
    ("browser.tabs.manage", {"action": "set_pinned", "tab": TAB, "pinned": False}),
    ("browser.windows.manage", {"action": "create"}),
    ("browser.windows.manage", {"action": "create", "url": "https://example.com/"}),
    ("browser.windows.manage", {"action": "focus", "window_id": 0}),
    ("browser.windows.manage", {"action": "close", "window_id": 0, "expected_tab_ids": [2, 1]}),
    ("browser.navigation.go", {"action": "navigate", "tab": TAB, "url": "https://example.com/"}),
    *[("browser.navigation.go", {"action": action, "tab": TAB}) for action in ("reload", "back", "forward")],
]


def test_every_action_required_unrelated_and_null_field_contract_matches_schema_and_js():
    entries = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries
    cases, expected = [], []
    for name, arguments in OPERATIONS:
        model = entries[name].arguments
        schema = model.model_json_schema()
        probes = [arguments, {**arguments, "action": "UNKNOWN"}, {**arguments, "extra": True}]
        for field in schema["properties"]:
            probes.extend([{**arguments, field: value} for value in (None, [], {}, True, "1", -2, 2**53)])
            probes.append({key: value for key, value in arguments.items() if key != field})
        if "expected_tab_ids" in arguments:
            probes.extend({**arguments, "expected_tab_ids": value} for value in
                          ([], [0], [1, 1], [True], [-1], [2**53], ["1"]))
        for probe in probes:
            try:
                wire = model.model_validate(probe).model_dump(exclude_none=True)
                valid = True
            except ValidationError:
                wire, valid = probe, False
            assert jsonschema.Draft202012Validator(schema).is_valid(probe) is valid, (name, probe)
            cases.append({"name": name, "arguments": wire})
            expected.append(valid)
    node = shutil.which("node")
    assert node, "Node is required to verify the Phase 4A wire contract"
    completed = subprocess.run([node, str(Path(__file__).resolve().parents[1] / "browser-extension/tests/contract-helper.mjs")],
                               input=json.dumps(cases), text=True, encoding="utf-8", capture_output=True, check=True, timeout=30)
    actual = json.loads(completed.stdout)["accepted"]
    assert actual == expected, [case for case, py, js in zip(cases, expected, actual) if py != js]


@pytest.mark.parametrize("name,arguments", OPERATIONS)
@pytest.mark.parametrize("interruption", ["disconnect", "timeout", "chrome_error"])
def test_side_effects_cross_bridge_once_and_are_never_replayed(tmp_path, name, arguments, interruption):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=[name]) as ws:
                bridge.request_timeout = 0.1 if interruption == "timeout" else 2
                pending = asyncio.create_task(registry.execute(name, arguments))
                request = json.loads(await ws.recv())
                assert request["capability"] == name
                assert request["arguments"] == arguments
                if interruption == "disconnect":
                    await ws.close()
                    code = "capability_unavailable"
                elif interruption == "chrome_error":
                    await ws.send(response(request, error={"code": "browser_operation_failed", "message": "PRIVATE"}))
                    code = "browser_operation_failed"
                else:
                    code = "capability_timeout"
                result = await pending
                assert result["error"]["code"] == code
                assert "PRIVATE" not in json.dumps(result)
                assert not bridge._pending
                if interruption != "disconnect":
                    with pytest.raises(TimeoutError):
                        async with asyncio.timeout(0.03):
                            await ws.recv()
            async with extension(bridge, capabilities=[name]) as ws:
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.03):
                        await ws.recv()
    run(scenario())


@pytest.mark.parametrize("code", ["window_not_found", "unsupported_window", "window_changed", "history_unavailable"])
def test_new_errors_keep_fixed_sanitized_contract(code):
    cleaned = extension_failure({"code": code, "message": "PRIVATE", "details": {"tabs": ["PRIVATE"]}})
    assert cleaned["error"]["code"] == code
    assert "PRIVATE" not in json.dumps(cleaned)


def test_close_confirmation_and_navigation_limits_are_discoverable():
    full = CapabilityRegistry(BrowserBridge(BrowserConfig())).discover(detail="full")["result"]
    assert "user confirmation" in full["conventions"]["window_close"]
    assert "no atomic" in full["conventions"]["window_close"]
    assert "navigation_waited=false" in full["conventions"]["navigation"]
    names = {entry["name"] for entry in full["capabilities"]}
    assert len(names) == 24
    assert not any("bookmark" in name for name in names)
    assert {"browser.groups.list", "browser.groups.manage", "browser.navigation.status", "browser.navigation.wait"} <= names


@pytest.mark.parametrize("arguments,valid", [
    ({}, True), ({"limit": 1, "tab_limit": 1}, True),
    ({"window_id": 0, "tab_offset": 50, "tab_snapshot": "a" * 64}, True),
    ({"window_id": 0, "tab_snapshot": "a" * 64}, True),
    ({"tab_offset": 50}, False), ({"window_id": 0, "tab_offset": 50}, False),
    ({"tab_snapshot": "a" * 64}, False),
    ({"window_id": None, "tab_offset": 50, "tab_snapshot": "a" * 64}, False),
    ({"window_id": 0, "tab_offset": 50, "tab_snapshot": None}, False),
    ({"tab_offset": True}, False), ({"tab_offset": -1}, False), ({"tab_offset": 10001}, False),
    ({"tab_limit": 0}, False), ({"tab_limit": 51}, False), ({"tab_limit": True}, False),
    ({"window_id": 0, "tab_snapshot": "A" * 64}, False),
])
def test_window_pagination_schema_and_runtime_require_bound_continuation(arguments, valid):
    model = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries["browser.windows.list"].arguments
    assert jsonschema.Draft202012Validator(model.model_json_schema()).is_valid(arguments) is valid
    if valid:
        model.model_validate(arguments)
    else:
        with pytest.raises(ValidationError):
            model.model_validate(arguments)


def test_window_pagination_defaults_and_snapshot_cross_existing_bridge(tmp_path):
    async def scenario():
        name = "browser.windows.list"
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=[name]) as ws:
                for arguments, expected in (
                    ({"window_id": 7}, {"window_id": 7, "limit": 20, "tab_offset": 0, "tab_limit": 50}),
                    ({"window_id": 7, "tab_offset": 50, "tab_snapshot": "a" * 64},
                     {"window_id": 7, "limit": 20, "tab_offset": 50, "tab_limit": 50, "tab_snapshot": "a" * 64}),
                ):
                    pending = asyncio.create_task(registry.execute(name, arguments))
                    request = json.loads(await ws.recv())
                    assert request["arguments"] == expected
                    await ws.send(response(request, error={"code": "window_changed", "message": "PRIVATE"}))
                    assert (await pending)["error"]["code"] == "window_changed"
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.03):
                        await ws.recv()
    run(scenario())
