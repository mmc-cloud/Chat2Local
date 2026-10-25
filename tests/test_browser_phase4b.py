"""Phase 4B contracts, bounded partial effects and transport recovery without replay."""
import ast
import asyncio
import io
import json
import re
from pathlib import Path
from zipfile import ZipFile

import jsonschema
import pytest
from conftest import run
from pydantic import ValidationError
from test_browser_bridge import browser_config, extension, response, running_bridge

from chat2local.browser.bridge import BrowserBridge
from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.browser.errors import extension_failure
from chat2local.mcp.output_schema import OUTPUT_SCHEMAS
from chat2local.runtime.config import BrowserConfig

NAMES = ["browser.navigation.status", "browser.navigation.wait", "browser.groups.list", "browser.groups.manage"]
BASELINE_ID = "url_00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("arguments", [
    {"action": "create", "tab_ids": []}, {"action": "create", "tab_ids": [1, 1]},
    {"action": "create", "tab_ids": [True]}, {"action": "create", "tab_ids": [-1]},
    {"action": "create", "tab_ids": [1], "group_id": None},
    {"action": "update", "group_id": 1}, {"action": "update", "group_id": 1, "title": None},
    {"action": "update", "group_id": 1, "color": "BLUE"},
    {"action": "update", "group_id": 1, "collapsed": 1},
    {"action": "move", "group_id": 1, "index": -2},
    {"action": "add_tabs", "group_id": 1, "tab_ids": [1], "window_id": 7},
    {"action": "remove_tabs", "tab_ids": [1]}, {"action": "delete", "group_id": 1},
])
def test_strict_group_actions_reject_before_transport(arguments):
    registry = CapabilityRegistry(BrowserBridge(BrowserConfig()))
    model = registry._entries[NAMES[3]].arguments
    assert not jsonschema.Draft202012Validator(model.model_json_schema()).is_valid(arguments)
    assert run(registry.execute(NAMES[3], arguments))["error"]["code"] == "invalid_arguments"


@pytest.mark.parametrize("arguments", [
    {"tab_id": 1, "condition": "url"},
    {"tab_id": 1, "condition": "url_changed", "url": "https://example.com/"},
    {"tab_id": 1, "condition": "navigation_complete", "url": "https://example.com/"},
    {"tab_id": 1, "condition": "current_complete", "url": "https://example.com/", "navigation_id": None},
    {"tab_id": True, "condition": "url", "url": "https://example.com/"},
    {"tab_id": 1, "condition": "url", "url": "https://example.com/", "timeout_ms": 8001},
    {"tab_id": 1, "condition": "url", "url": "https://example.com/", "baseline_url": None},
    {"tab_id": 1, "condition": "url_changed"},
    {"tab_id": 1, "condition": "url_changed", "url_baseline_id": None},
    {"tab_id": 1, "condition": "url_changed", "url_baseline_id": True},
    {"tab_id": 1, "condition": "url_changed", "url_baseline_id": "https://example.com/?token=PRIVATE"},
    {"tab_id": 1, "condition": "url_changed", "url_baseline_id": BASELINE_ID, "baseline_url": "https://example.com/"},
    {"tab_id": 1, "condition": "url_changed", "url_baseline_id": BASELINE_ID, "baseline_url": None},
    {"tab_id": 1, "condition": "url_changed", "url_baseline_id": BASELINE_ID, "url": "x"},
    {"tab_id": 1, "condition": "url", "url": "x", "url_baseline_id": BASELINE_ID},
])
def test_precise_navigation_wait_conditions_match_schema(arguments):
    model = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries[NAMES[1]].arguments
    assert not jsonschema.Draft202012Validator(model.model_json_schema()).is_valid(arguments)
    with pytest.raises(ValidationError):
        model.model_validate(arguments)


def test_navigation_wait_defaults_and_limits_are_below_bridge_timeout():
    model = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries[NAMES[1]].arguments
    assert model.model_validate({"tab_id": 1, "condition": "url", "url": "x"}).timeout_ms == 5000
    assert model.model_json_schema()["properties"]["timeout_ms"]["maximum"] == 8000


@pytest.mark.parametrize("baseline", [{"url_baseline_id": BASELINE_ID}, {"baseline_url": "https://example.com/?token=PRIVATE#part"}])
def test_url_change_accepts_exactly_one_baseline(baseline):
    model = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries[NAMES[1]].arguments
    arguments = {"tab_id": 1, "condition": "url_changed", **baseline}
    assert jsonschema.Draft202012Validator(model.model_json_schema()).is_valid(arguments)
    assert model.model_validate(arguments).model_dump(exclude_none=True) == {**arguments, "timeout_ms": 5000}


def test_unavailable_baseline_error_is_explicit_sanitized_and_schema_valid():
    result = extension_failure({"code": "url_baseline_unavailable", "message": "https://example.com/?token=PRIVATE",
                                "details": {"url": "PRIVATE", "baseline_url": "PRIVATE"}})
    assert result["error"]["code"] == "url_baseline_unavailable"
    assert "PRIVATE" not in json.dumps(result)
    assert "details" not in result["error"]
    jsonschema.validate(result, OUTPUT_SCHEMAS["capability_execute"])


@pytest.mark.parametrize("progress", [
    {"completed_steps": ["grouped"], "outcome": "partial", "group_id": 3, "tab_ids": [1, 2]},
    {"completed_steps": [], "outcome": "unknown", "tab_ids": [1]},
])
def test_partial_progress_is_bounded_sanitized_and_public_schema_valid(progress):
    result = extension_failure({"code": "browser_operation_failed", "message": "PRIVATE", "details": {
        "progress": {**progress, "title": "PRIVATE", "exception": "PRIVATE"}, "secret": "PRIVATE"}})
    assert result["error"]["details"]["progress"] == progress
    assert "PRIVATE" not in json.dumps(result)
    jsonschema.validate(result, OUTPUT_SCHEMAS["capability_execute"])


@pytest.mark.parametrize("progress", [None, [], {"completed_steps": ["PRIVATE"], "outcome": "partial"},
    {"completed_steps": ["grouped"] * 5, "outcome": "partial"},
    {"completed_steps": [], "outcome": "PRIVATE"}])
def test_malformed_progress_is_not_forwarded(progress):
    result = extension_failure({"code": "browser_operation_failed", "details": {"progress": progress}})
    assert "details" not in result["error"]


@pytest.mark.parametrize("name,args", [
    (NAMES[0], {"tab": {"tab_id": 1}}),
    (NAMES[1], {"tab_id": 1, "condition": "url", "url": "https://example.com/", "timeout_ms": 0}),
    (NAMES[1], {"tab_id": 1, "condition": "url_changed", "url_baseline_id": BASELINE_ID, "timeout_ms": 0}),
    (NAMES[2], {}), (NAMES[3], {"action": "create", "tab_ids": [1], "color": "blue"}),
])
def test_new_capabilities_and_partial_results_cross_actual_bridge_without_replay(tmp_path, name, args):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=NAMES) as ws:
                pending = asyncio.create_task(registry.execute(name, args))
                request = json.loads(await ws.recv())
                assert request["capability"] == name
                if "url_baseline_id" in args:
                    assert request["arguments"]["url_baseline_id"] == BASELINE_ID
                    assert "baseline_url" not in request["arguments"]
                if name == NAMES[3]:
                    await ws.send(response(request, error={"code": "groups_permission_denied", "details": {"progress": {
                        "completed_steps": ["grouped"], "outcome": "partial", "group_id": 9, "tab_ids": [1], "title": "PRIVATE"}}}))
                    result = await pending
                    assert result["error"]["details"]["progress"]["group_id"] == 9
                    assert "PRIVATE" not in json.dumps(result)
                else:
                    await ws.close()
                    assert (await pending)["error"]["code"] == "capability_unavailable"
            async with extension(bridge, capabilities=NAMES) as ws:
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.03):
                        await ws.recv()
    run(scenario())


def test_wait_timeout_never_sends_navigation_or_replays_after_reconnect(tmp_path):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=NAMES) as ws:
                bridge.request_timeout = 0.03
                pending = asyncio.create_task(registry.execute(NAMES[1], {"tab_id": 1, "condition": "current_complete", "url": "https://example.com/"}))
                assert json.loads(await ws.recv())["capability"] == NAMES[1]
                assert (await pending)["error"]["code"] == "capability_timeout"
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.03):
                        await ws.recv()
    run(scenario())


def test_runtime_scripts_are_loaded_and_present_in_explicit_release_zip_list():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    names = ast.literal_eval(re.search(r"names = (\([\s\S]*?\))", workflow).group(1))
    scripts = re.findall(r'importScripts\("([^"]+)"\)', (root / "browser-extension/background.js").read_text(encoding="utf-8"))
    assert {"browser-navigation.js", "browser-groups.js"} <= set(scripts) <= set(names)
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name in names:
            archive.writestr(name, (root / "browser-extension" / name).read_bytes())
    with ZipFile(buffer) as archive:
        assert set(names) == set(archive.namelist())
    manifest = json.loads((root / "browser-extension/manifest.json").read_text(encoding="utf-8"))
    assert manifest["optional_permissions"] == ["tabGroups"]
    assert "webNavigation" not in manifest["permissions"]
    assert "tabGroups" not in manifest["permissions"]
