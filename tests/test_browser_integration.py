"""HTTP MCP -> Hub -> two real Agent sockets -> simulated extension sockets."""

import asyncio
from contextlib import AsyncExitStack
import json

import pytest

from chat2local.agent.client import AgentClient
from chat2local.app import create_app
from chat2local.browser.bridge import CAPABILITY
from chat2local.browser.errors import CAPABILITY_NAMES
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import AppConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run
from test_browser_bridge import browser_config, extension, response
from test_devices import TOKEN, decode, eventually, hub_runtime, running_agent, running_server
from test_process_integration import mcp_client


def test_mcp_hub_agent_browser_device_isolation_and_disconnect(tmp_path):
    roots = [tmp_path / name for name in ("hub", "laptop", "desktop")]
    for root in roots:
        root.mkdir()
        (root / "file.txt").write_text(root.name, encoding="utf-8")

    async def scenario():
        router = hub_runtime(roots[0])
        agents = [LocalToolDispatcher(WorkspaceManager(root), AppConfig(browser=browser_config(tmp_path, name=root.name)))
                  for root in roots[1:]]
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url), AsyncExitStack() as stack:
            for root, local in zip(roots[1:], agents):
                await stack.enter_async_context(running_agent(AgentClient(ws_url, root.name, TOKEN, local)))
            await eventually(lambda: len(router.registry.sessions) == 2)
            async with mcp_client(url) as client:
                for device in ("hub", "laptop", "desktop"):
                    discovered = decode(await client.call_tool("capability_discover", {"device": device}))
                    assert discovered["result"]["capabilities"][0]["available"] is False
                    assert decode(await client.call_tool("read", {"device": device, "path": "file.txt"}))["text"] == device
                sockets = [await stack.enter_async_context(extension(local.browser)) for local in agents]
                for index, device in enumerate(("laptop", "desktop")):
                    discovered = decode(await client.call_tool("capability_discover", {"device": device, "query": "Chrome"}))
                    assert discovered["result"]["capabilities"][0]["available"] is True
                    assert discovered["result"]["capabilities"][0]["input_schema"]["required"] == ["url"]
                    pending = asyncio.create_task(client.call_tool("capability_execute", {
                        "device": device, "capability": CAPABILITY, "arguments": {"url": f"https://example.com/{device}"},
                    }))
                    request = json.loads(await asyncio.wait_for(sockets[index].recv(), 3))
                    assert request["arguments"]["url"].endswith(device)
                    assert not agents[1 - index].browser._pending  # Other device received no request.
                    await sockets[index].send(response(request, result={"tab_id": index + 1}))
                    assert decode(await pending) == {"ok": True, "result": {"tab_id": index + 1}}
                for code in ("request_expired", "browser_operation_failed"):
                    pending = asyncio.create_task(client.call_tool("capability_execute", {
                        "device": "laptop", "capability": CAPABILITY, "arguments": {"url": "https://example.com"},
                    }))
                    request = json.loads(await asyncio.wait_for(sockets[0].recv(), 3))
                    await sockets[0].send(response(request, error={"code": code, "message": "PRIVATE-DIAGNOSTIC"}))
                    result = decode(await pending)
                    assert result["ok"] is False and result["error"]["code"] == code
                    assert "PRIVATE-DIAGNOSTIC" not in json.dumps(result)
                for args, code in (({}, "invalid_arguments"), ({"url": "file:///a"}, "invalid_arguments")):
                    result = decode(await client.call_tool("capability_execute", {"device": "laptop", "capability": CAPABILITY, "arguments": args}))
                    assert result["error"]["code"] == code
                assert not agents[0].browser._pending
                await sockets[0].close()
                await eventually(lambda: not agents[0].browser.available)
                listing = decode(await client.call_tool("capability_discover", {"device": "laptop"}))
                assert listing["result"]["capabilities"][0]["available"] is False
                result = decode(await client.call_tool("capability_execute", {
                    "device": "laptop", "capability": CAPABILITY, "arguments": {"url": "https://example.com"},
                }))
                assert result["error"]["code"] == "capability_unavailable"
                assert agents[1].browser.available
                assert decode(await client.call_tool("read", {"device": "laptop", "path": "file.txt"}))["text"] == "laptop"
                missing = decode(await client.call_tool("capability_execute", {
                    "device": "missing", "capability": CAPABILITY, "arguments": {"url": "https://example.com"},
                }))
                assert missing["error"]["code"] == "device_unavailable"
        assert all(local.browser.server is None and not local.browser.available for local in agents)
        assert router.local.browser.server is None
    run(scenario())


@pytest.mark.parametrize("hub", [False, True])
def test_server_lifespan_owns_optional_local_bridge(tmp_path, hub):
    async def scenario():
        local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig(browser=browser_config(tmp_path)))
        router = hub_runtime(tmp_path) if hub else DeviceRouter("local", local)
        router.local = local
        async with running_server(create_app(router, hub_token=TOKEN if hub else None)):
            assert local.browser.server is not None
            async with extension(local.browser):
                assert local.capabilities.discover()["result"]["capabilities"][0]["available"] is True
        assert local.browser.server is None and not local.browser.available
    run(scenario())


def test_phase2_capabilities_route_to_only_the_selected_agent(tmp_path):
    roots = [tmp_path / name for name in ("hub", "first", "second")]
    for root in roots:
        root.mkdir()
    async def scenario():
        router = hub_runtime(roots[0])
        locals_ = [LocalToolDispatcher(WorkspaceManager(root), AppConfig(browser=browser_config(tmp_path, name=root.name)))
                   for root in roots[1:]]
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url), AsyncExitStack() as stack:
            for root, local in zip(roots[1:], locals_):
                await stack.enter_async_context(running_agent(AgentClient(ws_url, root.name, TOKEN, local)))
            await eventually(lambda: len(router.registry.sessions) == 2)
            sockets = [await stack.enter_async_context(extension(local.browser, capabilities=list(CAPABILITY_NAMES))) for local in locals_]
            async with mcp_client(url) as client:
                for index, device in enumerate(("first", "second")):
                    listing = decode(await client.call_tool("capability_discover", {"device": device}))
                    assert all(entry["available"] for entry in listing["result"]["capabilities"])
                    for capability, arguments in (
                        ("browser.tabs.list", {}),
                        ("browser.tabs.resolve", {"tab": {"tab_id": 7}}),
                        ("browser.page.inspect", {"tab": {"tab_id": 7}, "target": {"role": "button"}}),
                        ("browser.page.click", {"tab": {"tab_id": 7}, "target": {"name": "Go"}}),
                        ("browser.page.fill", {"tab": {"tab_id": 7}, "target": {"label": "Query"}, "value": "text"}),
                    ):
                        pending = asyncio.create_task(client.call_tool("capability_execute", {
                            "device": device, "capability": capability, "arguments": arguments,
                        }))
                        request = json.loads(await asyncio.wait_for(sockets[index].recv(), 3))
                        assert request["capability"] == capability
                        assert not locals_[1 - index].browser._pending
                        await sockets[index].send(response(request, result={"device": device}))
                        assert decode(await pending)["result"]["device"] == device
                pending = asyncio.create_task(client.call_tool("capability_execute", {
                    "device": "first", "capability": "browser.page.click",
                    "arguments": {"tab": {"tab_id": 7}, "target": {"name": "Duplicate"}},
                }))
                request = json.loads(await sockets[0].recv())
                await sockets[0].send(response(request, error={"code": "ambiguous_target", "message": "PRIVATE",
                    "details": {"candidates": [{"name": "Duplicate", "role": "button", "extra": "PRIVATE"}]}}))
                error = decode(await pending)["error"]
                assert error["details"]["candidates"] == [{"role": "button", "name": "Duplicate"}]
                assert "PRIVATE" not in json.dumps(error)
                await sockets[0].close()
                await eventually(lambda: not locals_[0].browser.is_available("browser.page.click"))
                assert locals_[1].browser.is_available("browser.page.click")
    run(scenario())
