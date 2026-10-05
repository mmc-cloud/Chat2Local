"""Strict dispatch and real standalone / Hub-local / Hub-WS-Agent MCP calls."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from chat2local.agent.client import AgentClient
from chat2local.app import create_app
from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.handoff import store as store_module
from chat2local.hub.router import DeviceRouter
from chat2local.runtime.config import AppConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import handoff_directory, run
from test_devices import TOKEN, decode, eventually, hub_runtime, running_agent, running_server
from test_process_integration import mcp_client
from test_process_tools import LOCAL_TOOLS
from test_handoff_store import INVALID_DISCOVERY


@pytest.mark.parametrize("tool,arguments,message", [
    ("handoff_list", {"unknown": True}, "Invalid arguments"),
    ("handoff_list", {"device": "other"}, "Invalid arguments"),
    ("handoff_get", {"workstream": "a", "path": "other.md"}, "Invalid arguments"),
    ("handoff_get", {}, "Invalid arguments"),
    ("handoff_get", {"workstream": "../x"}, "invalid_workstream:"),
    ("handoff_get", {"workstream": 1}, "Invalid arguments"),
    ("handoff_save", {"title": "Title", "summary": "Summary", "workstream": "a", "content": "body"}, "Invalid arguments"),
    *[("handoff_save", {"title": "Title", "summary": "Summary", "workstream": "a", "content": "body", "expected_revision": rev}, "Invalid arguments")
      for rev in (True, "0", 0.0, -1, None)],
    ("handoff_save", {"title": "Title", "summary": "Summary", "workstream": "a", "content": 1, "expected_revision": 0}, "Invalid arguments"),
    ("handoff_save", {"title": "Title", "summary": "Summary", "workstream": "a", "content": "body", "expected_revision": 0, "extra": 1}, "Invalid arguments"),
])
def test_dispatcher_strict_handoff_arguments(tmp_path, tool, arguments, message):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    with pytest.raises(ToolExecutionError, match=message):
        run(local.execute(tool, arguments))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field,value", INVALID_DISCOVERY)
def test_dispatcher_rejects_invalid_title_summary(tmp_path, field, value):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    arguments = dict(workstream="design", title="Title", summary="Summary", content="body", expected_revision=0)
    arguments[field] = value
    with pytest.raises(ToolExecutionError, match=field):
        run(local.execute("handoff_save", arguments))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field", ["title", "summary"])
def test_dispatcher_requires_discovery_metadata(tmp_path, field):
    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    arguments = dict(workstream="design", title="Title", summary="Summary", content="body", expected_revision=0)
    del arguments[field]
    with pytest.raises(ToolExecutionError, match=field):
        run(local.execute("handoff_save", arguments))
    assert list(tmp_path.iterdir()) == []


def test_dispatcher_workspace_override_and_concurrency(tmp_path):
    a, b, outside = (tmp_path / name for name in ("A", "B", "outside"))
    for path in (a, b, outside):
        path.mkdir()
    manager = WorkspaceManager(a, allowed_roots=[a, b])
    local = LocalToolDispatcher(manager, AppConfig())
    assert local.tools == LOCAL_TOOLS and len(local.tools) == 9
    async def scenario():
        store = local.handoff_store
        args = {"title": "Title", "summary": "Summary", "workstream": "design", "content": "original", "expected_revision": 0, "workspace": str(b)}
        assert (await local.execute("handoff_save", args))["revision"] == 1
        assert await local.execute("handoff_list", {}) == {"handoffs": []}
        assert (await local.execute("handoff_get", {"workstream": "design", "workspace": str(b)}))["content"] == "original"
        assert len((await local.execute("handoff_list", {"workspace": str(b)}))["handoffs"]) == 1
        results = await asyncio.gather(*[
            local.execute("handoff_save", {**args, "title": f"Title {content}", "summary": f"Summary {content}",
                                           "content": content, "expected_revision": 1})
            for content in ("A", "B")
        ], return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, ToolExecutionError) and "revision_conflict:" in str(result) for result in results) == 1
        winner = next(result for result in results if isinstance(result, dict))
        record = await local.execute("handoff_get", {"workstream": "design", "workspace": str(b)})
        assert record == {**winner, "content": winner["title"][-1]}
        assert winner["summary"] == f"Summary {record['content']}"
        for tool, extra in (("handoff_list", {}), ("handoff_get", {"workstream": "design"}), ("handoff_save", args)):
            for workspace, message in ((str(outside), "outside allowed roots"), ("B", "absolute path")):
                with pytest.raises(ToolExecutionError, match=message):
                    await local.execute(tool, {**extra, "workspace": workspace})
        assert local.handoff_store is store and local.workspace is manager
    run(scenario())
    assert list(a.iterdir()) == [] and list(outside.iterdir()) == []


@pytest.mark.parametrize("mode", ["standalone", "hub-local", "agent"])
def test_real_mcp_handoff_flow_schema_and_concurrent_conflict(tmp_path, mode):
    hub, agent_root, selected, outside = (tmp_path / name for name in ("hub", "agent", "selected", "outside"))
    for root in (hub, agent_root, selected, outside):
        root.mkdir()
    remote = mode == "agent"
    target = agent_root if remote else hub
    device = "desktop" if remote else None

    async def scenario():
        router = hub_runtime(hub) if mode != "standalone" else DeviceRouter(
            "hub", LocalToolDispatcher(WorkspaceManager(hub, allowed_roots=[hub, selected]), AppConfig()),
        )
        if mode == "hub-local":
            router.local = LocalToolDispatcher(WorkspaceManager(hub, allowed_roots=[hub, selected]), AppConfig())
        agent = LocalToolDispatcher(WorkspaceManager(agent_root, allowed_roots=[agent_root, selected]), AppConfig())
        async with running_server(create_app(router, hub_token=TOKEN if mode != "standalone" else None)) as (url, ws_url):
            @asynccontextmanager
            async def connected():
                if remote:
                    async with running_agent(AgentClient(ws_url, "desktop", TOKEN, agent)):
                        await eventually(lambda: "desktop" in router.registry.sessions)
                        yield
                else:
                    yield
            async with connected(), mcp_client(url) as client:
                tools = {tool.name: tool.input_schema for tool in (await client.list_tools()).tools}
                assert set(tools) == set(LOCAL_TOOLS) | {"list_devices"} and len(tools) == 10
                for name, fields, required in (
                    ("handoff_list", {"workspace", "device"}, []),
                    ("handoff_get", {"workstream", "workspace", "device"}, ["workstream"]),
                    ("handoff_save", {"workstream", "title", "summary", "content", "expected_revision", "workspace", "device"},
                     ["workstream", "title", "summary", "content", "expected_revision"]),
                ):
                    assert set(tools[name]["properties"]) == fields
                    assert tools[name].get("required", []) == required
                assert tools["handoff_get"]["properties"]["workstream"]["pattern"] == store_module.WORKSTREAM_PATTERN
                assert tools["handoff_save"]["properties"]["expected_revision"]["minimum"] == 0
                for field, maximum in (("title", 120), ("summary", 500)):
                    assert tools["handoff_save"]["properties"][field]["type"] == "string"
                    assert tools["handoff_save"]["properties"][field]["minLength"] == 1
                    assert tools["handoff_save"]["properties"][field]["maxLength"] == maximum
                listing = decode(await client.call_tool("list_devices", {}))
                assert all(entry["tools"] == list(LOCAL_TOOLS) for entry in listing["devices"])
                if remote:
                    assert router.registry.get("desktop").capabilities == LOCAL_TOOLS
                    assert "list_devices" not in agent.tools
                async def call(name, **args):
                    return await client.call_tool(name, {**args, "device": device})
                assert decode(await call("handoff_list")) == {"handoffs": []}
                assert list(target.iterdir()) == []
                created = decode(await call("handoff_save", title="阶段四：跨会话续接 🚀", summary="State: implemented; next: 验证跨 Chat resume。", workstream="design", content="正文😀", expected_revision=0))
                assert created["revision"] == 1
                assert created["title"] == "阶段四：跨会话续接 🚀"
                assert created["summary"] == "State: implemented; next: 验证跨 Chat resume。"
                assert (handoff_directory(target) / "design.md").read_bytes().endswith("正文😀".encode())
                assert decode(await call("handoff_get", workstream="design")) == {**created, "content": "正文😀"}
                assert decode(await call("handoff_list")) == {"handoffs": [created]}
                results = await asyncio.gather(*[
                    call("handoff_save", title=f"Title {body}", summary=f"Summary {body}", workstream="design", content=body, expected_revision=1)
                    for body in ("A", "B")
                ])
                winners = [i for i, result in enumerate(results) if not result.is_error]
                assert len(winners) == 1 and decode(results[winners[0]])["revision"] == 2
                assert "revision_conflict:" in results[1 - winners[0]].content[0].text
                winner = ("A", "B")[winners[0]]
                winner_metadata = decode(results[winners[0]])
                assert winner_metadata["title"] == f"Title {winner}"
                assert winner_metadata["summary"] == f"Summary {winner}"
                assert decode(await call("handoff_get", workstream="design")) == {**winner_metadata, "content": winner}
                # Overrides use the target's permissions and never mutate its default.
                assert decode(await call("handoff_save", title="Title", summary="Summary", workstream="selected", content="selected", expected_revision=0,
                                         workspace=str(selected)))["revision"] == 1
                assert decode(await call("handoff_get", workstream="selected", workspace=str(selected)))["content"] == "selected"
                assert len(decode(await call("handoff_list", workspace=str(selected)))["handoffs"]) == 1
                assert len(decode(await call("handoff_list"))["handoffs"]) == 1
                for name, args, message in (
                    ("handoff_get", {"workstream": "missing"}, "handoff_not_found:"),
                    ("handoff_save", {"title": "Title", "summary": "Summary", "workstream": "design", "content": "bad", "expected_revision": 0}, "revision_conflict:"),
                    ("handoff_save", {"title": "Title", "summary": "Summary", "workstream": "missing", "content": "bad", "expected_revision": 5}, "revision_conflict:"),
                    ("handoff_get", {"workstream": "../x"}, "invalid_workstream:"),
                    ("handoff_list", {"workspace": str(outside)}, "outside allowed roots"),
                    ("handoff_list", {"workspace": "relative"}, "absolute path"),
                ):
                    error = await call(name, **args)
                    assert error.is_error and message in error.content[0].text
                for rev in (True, "0", 0.0, -1):
                    assert (await call("handoff_save", title="Title", summary="Summary", workstream="bad", content="body", expected_revision=rev)).is_error
                for field, value in INVALID_DISCOVERY:
                    args = dict(workstream="bad", title="Title", summary="Summary", content="body", expected_revision=0)
                    args[field] = value
                    assert (await call("handoff_save", **args)).is_error
                for field in ("title", "summary"):
                    args = dict(workstream="bad", title="Title", summary="Summary", content="body", expected_revision=0)
                    del args[field]
                    assert (await call("handoff_save", **args)).is_error
                assert not (handoff_directory(target) / "bad.md").exists()
                if mode == "standalone":
                    unavailable = await client.call_tool("handoff_list", {"device": "remote"})
                    assert unavailable.is_error and "unavailable" in unavailable.content[0].text
                # Corrupt metadata is transported as a tool error, including over WS.
                broken_path = handoff_directory(target) / "broken.md"
                for data in (b"broken", b"---\nrevision: 1\nupdated_at: 2026-10-03T02:30:00Z\n---\n\nbody"):
                    broken_path.write_bytes(data)
                    for name, args in (("handoff_list", {}), ("handoff_get", {"workstream": "broken"}),
                                       ("handoff_save", {"title": "Title", "summary": "Summary", "workstream": "broken", "content": "new", "expected_revision": 1})):
                        error = await call(name, **args)
                        assert error.is_error and "invalid_handoff:" in error.content[0].text
                        assert broken_path.read_bytes() == data
                if remote:
                    assert list(hub.iterdir()) == []
                    # Reconnect preserves the same dispatcher/store and persisted revision.
                    store = agent.handoff_store
                    session = router.registry.get("desktop")
                    await session.websocket.close(code=1001)
                    await eventually(lambda: router.registry.sessions.get("desktop") is not None
                                     and router.registry.sessions["desktop"] is not session)
                    assert agent.handoff_store is store
                    assert decode(await call("handoff_get", workstream="design")) == {**winner_metadata, "content": winner}
        assert router.local.workspace.root == hub.resolve()
    run(scenario())
