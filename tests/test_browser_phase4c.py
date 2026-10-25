"""Bookmark contracts, privacy, device bridge delivery and non-replay guarantees."""
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
from chat2local.browser.errors import (
    CAPABILITY_NAMES,
    ERROR_GUIDANCE,
    EXTENSION_ERROR_MESSAGES,
    extension_failure,
)
from chat2local.mcp.metadata import TOOL_METADATA
from chat2local.mcp.output_schema import OUTPUT_SCHEMAS
from chat2local.runtime.config import BrowserConfig

ROOT = Path(__file__).resolve().parents[1]
NAMES = ["browser.bookmarks." + name for name in ("query", "manage", "preview_remove_tree", "remove_tree")]
PREVIEW = "bmk_00000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize("name,arguments", [
    (NAMES[0], {}), (NAMES[0], {"action": "get"}), (NAMES[0], {"action": "get", "bookmark_id": 1}),
    (NAMES[0], {"action": "get", "bookmark_id": "1", "limit": 1}),
    (NAMES[0], {"action": "children", "folder_id": None}),
    (NAMES[0], {"action": "children", "folder_id": "1", "offset": 50}),
    (NAMES[0], {"action": "subtree", "folder_id": "1", "depth": 6}),
    (NAMES[0], {"action": "roots", "folder_id": "1"}),
    (NAMES[0], {"action": "search"}), (NAMES[0], {"action": "search", "title": ""}),
    (NAMES[0], {"action": "recent", "limit": 51}), (NAMES[0], {"action": "roots", "snapshot": None}),
    (NAMES[1], {"action": "create", "kind": "bookmark", "parent_id": "1", "title": "New"}),
    (NAMES[1], {"action": "create", "kind": "folder", "parent_id": "1", "title": "New", "url": "https://example.com/"}),
    (NAMES[1], {"action": "create", "kind": "folder", "title": "New"}),
    (NAMES[1], {"action": "create", "parent_id": "1", "title": "New"}),
    (NAMES[1], {"action": "create", "kind": "folder", "parent_id": "1", "title": "New", "bookmark_id": None}),
    (NAMES[1], {"action": "update", "bookmark_id": "1"}),
    (NAMES[1], {"action": "update", "bookmark_id": "1", "title": None}),
    (NAMES[1], {"action": "move", "bookmark_id": "1"}),
    (NAMES[1], {"action": "move", "bookmark_id": "1", "parent_id": "2", "index": -1}),
    (NAMES[1], {"action": "remove", "bookmark_id": "1", "title": "New"}),
    (NAMES[1], {"action": "remove", "bookmark_id": "../1"}),
    (NAMES[1], {"action": "removeTree", "bookmark_id": "1"}),
    (NAMES[2], {"folder_id": "1", "examples_limit": 21}),
    (NAMES[2], {"folder_id": "1", "examples_limit": None}),
    (NAMES[3], {"folder_id": "1"}), (NAMES[3], {"folder_id": "1", "preview_id": None}),
    (NAMES[3], {"folder_id": "1", "preview_id": PREVIEW, "confirmed": True}),
])
def test_strict_bookmark_actions_match_schema_and_reject_before_transport(name, arguments):
    registry = CapabilityRegistry(BrowserBridge(BrowserConfig()))
    model = registry._entries[name].arguments
    assert not jsonschema.Draft202012Validator(model.model_json_schema()).is_valid(arguments)
    assert run(registry.execute(name, arguments))["error"]["code"] == "invalid_arguments"


def test_bookmark_url_write_semantics_match_js_without_exempting_schema_contracts():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for URL contract checks")
    model = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries[NAMES[1]].arguments
    cases, expected = [], []
    for url, accepted in [
        ("https://example.com/path?token=PRIVATE#fragment", True), ("http://localhost:8080/", True),
        ("chrome://bookmarks/", True), ("chrome-extension://abcdefghijklmnopqrstuvwxyzabcdef/options.html", True),
        ("file:///C:/temp/a.html", True), ("ftp://example.com/a", True), ("about:blank", True),
        ("javascript:alert(1)", False), ("data:text/html,hello", False), ("blob:https://example.com/1", False),
        ("https://user:pass@example.com/", False), ("https://@example.com/", False),
        ("https:///example.com/", False), ("https://[invalid/", False), ("https://example.com:65536/", False),
        ("https://example.com:0/", False), ("chrome://settings:123/", False), ("https://example.com/a b", False),
        ("https://example.com/\u200b", False), ("file://user:pass@host/a", False), ("relative/path", False),
    ]:
        args = {"action": "create", "kind": "bookmark", "parent_id": "1", "title": "Example", "url": url}
        if accepted:
            model.model_validate(args)
            assert jsonschema.Draft202012Validator(model.model_json_schema()).is_valid(args)
        else:
            with pytest.raises(ValidationError):
                model.model_validate(args)
        cases.append({"name": NAMES[1], "arguments": args})
        expected.append(accepted)
    result = subprocess.run([node, str(ROOT / "browser-extension/tests/contract-helper.mjs")], input=json.dumps(cases),
                            text=True, encoding="utf-8", capture_output=True, timeout=30, check=True)
    assert json.loads(result.stdout)["accepted"] == expected
    # Reading existing URLs is independent of the write allowlist.
    query = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries[NAMES[0]].arguments
    query.model_validate({"action": "search", "url": "javascript:existing-bookmark"})


@pytest.mark.parametrize("code", [code for code in EXTENSION_ERROR_MESSAGES if code.startswith(("bookmark", "bookmarks"))])
def test_bookmark_errors_never_expose_native_titles_urls_or_exception_details(code):
    cleaned = extension_failure({"code": code, "message": "PRIVATE-TITLE https://example.com/?token=PRIVATE",
                                 "details": {"url": "PRIVATE", "title": "PRIVATE", "exception": "PRIVATE"}})
    assert cleaned["error"]["code"] == code
    assert "PRIVATE" not in json.dumps(cleaned)
    assert "details" not in cleaned["error"]
    assert ERROR_GUIDANCE[code]
    jsonschema.validate(cleaned, OUTPUT_SCHEMAS["capability_execute"])


@pytest.mark.parametrize("name,arguments", [
    (NAMES[0], {"action": "roots"}),
    (NAMES[1], {"action": "create", "kind": "folder", "parent_id": "bar", "title": ""}),
    (NAMES[2], {"folder_id": "folder"}),
    (NAMES[3], {"folder_id": "folder", "preview_id": PREVIEW}),
])
def test_bookmark_capabilities_cross_real_bridge_preserving_string_ids(tmp_path, name, arguments):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=NAMES) as ws:
                pending = asyncio.create_task(registry.execute(name, arguments))
                request = json.loads(await ws.recv())
                assert request["capability"] == name
                assert request["arguments"] == {**arguments, **({"examples_limit": 10} if name == NAMES[2] else {})}
                await ws.send(response(request, result={"observed": True}))
                assert await pending == {"ok": True, "result": {"observed": True}}
    run(scenario())


@pytest.mark.parametrize("disconnect", [False, True])
def test_recursive_delete_timeout_disconnect_and_reconnect_never_replay(tmp_path, disconnect):
    async def scenario():
        async with running_bridge(browser_config(tmp_path)) as bridge:
            registry = CapabilityRegistry(bridge)
            async with extension(bridge, capabilities=NAMES) as ws:
                bridge.request_timeout = 0.03
                pending = asyncio.create_task(registry.execute(NAMES[3], {"folder_id": "folder", "preview_id": PREVIEW}))
                assert json.loads(await ws.recv())["capability"] == NAMES[3]
                if disconnect:
                    await ws.close()
                assert (await pending)["error"]["code"] == ("capability_unavailable" if disconnect else "capability_timeout")
            async with extension(bridge, capabilities=NAMES) as ws:
                with pytest.raises(TimeoutError):
                    async with asyncio.timeout(0.03):
                        await ws.recv()
    run(scenario())


def test_public_counts_bookmark_guidance_and_packaged_runtime_are_complete():
    registry = CapabilityRegistry(BrowserBridge(BrowserConfig()))
    assert len(TOOL_METADATA) == 12
    assert len(registry._entries) == len(CAPABILITY_NAMES) == 28
    assert set(NAMES) <= set(registry._entries)
    conventions = registry.discover(detail="full")["result"]["conventions"]
    assert "explicit confirmation in conversation" in conventions["bookmark_recursive_delete"]
    assert "not consent" in conventions["bookmark_recursive_delete"]
    background = (ROOT / "browser-extension/background.js").read_text(encoding="utf-8")
    assert background.index('importScripts("browser-bookmarks.js")') < background.index('importScripts("browser-operations.js")')
    assert '"browser-bookmarks.js"' in (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "browser-extension/manifest.json").read_text(encoding="utf-8"))
    assert "bookmarks" in manifest["optional_permissions"]
    assert not {"bookmarks", "history", "cookies", "debugger", "downloads"} & set(manifest["permissions"])
