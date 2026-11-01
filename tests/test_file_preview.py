"""Image bytes survive capability routing without expanding the public surface."""

import base64
import json

import pytest

from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.mcp.server import create_mcp_server
from chat2local.runtime.config import AppConfig, ReadConfig
from chat2local.runtime.workspace import WorkspaceManager
from conftest import run

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


def bound(tmp_path, config=None):
    return LocalToolDispatcher(WorkspaceManager(tmp_path), config or AppConfig())


def test_discovery_without_browser(tmp_path):
    local = bound(tmp_path)
    result = run(local.execute("capability_discover", {"query": "file.preview", "detail": "full"}))
    entry, = result["result"]["capabilities"]
    assert entry["name"] == "file.preview" and entry["available"] is True
    assert entry["input_schema"]["required"] == ["path"]
    assert "image/png" in entry["result_contract"]["formats"]
    assert local.browser.server is None


def test_mcp_image_and_unchanged_tools(tmp_path):
    (tmp_path / "plot.png").write_bytes(PNG)
    server = create_mcp_server(DeviceRouter("local", bound(tmp_path)))
    assert len(run(server.list_tools())) == 12
    result = run(server.call_tool("capability_execute", {
        "capability": "file.preview", "arguments": {"path": "plot.png"},
    }))
    metadata = json.loads(result.content[0].text)
    assert metadata == {"ok": True, "result": {"path": "plot.png", "mime_type": "image/png", "size": len(PNG)}}
    image, = [block for block in result.content if block.type == "image"]
    assert base64.b64decode(image.data) == PNG and image.mime_type == "image/png"
    assert result.structured_content == metadata


@pytest.mark.parametrize(("path", "code"), [("../outside.png", "outside_workspace"),
                                            ("missing.png", "not_found"), (".", "not_regular_file")])
def test_path_policy(tmp_path, path, code):
    result = run(bound(tmp_path).execute("capability_execute", {"capability": "file.preview", "arguments": {"path": path}}))
    assert result["error"]["code"] == code


@pytest.mark.parametrize("arguments", [{}, {"path": None}, {"path": 1}, {"path": "x", "max_bytes": 10**9}])
def test_invalid_arguments(tmp_path, arguments):
    result = run(bound(tmp_path).execute("capability_execute", {"capability": "file.preview", "arguments": arguments}))
    assert result["error"]["code"] == "invalid_arguments"


def test_device_limits_and_binary_signature(tmp_path):
    (tmp_path / "plot.png").write_bytes(PNG)
    local = bound(tmp_path, AppConfig(read=ReadConfig(max_bytes=8)))
    result = run(local.execute("capability_execute", {"capability": "file.preview", "arguments": {"path": "plot.png"}}))
    assert result["error"]["code"] == "preview_too_large"
    (tmp_path / "fake.png").write_text("not an image", encoding="utf-8")
    result = run(bound(tmp_path).execute("capability_execute", {"capability": "file.preview", "arguments": {"path": "fake.png"}}))
    assert result["error"]["code"] == "unsupported_format"


def test_workspace_selection_and_link_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    local = LocalToolDispatcher(WorkspaceManager(workspace), AppConfig())
    result = run(local.execute("capability_execute", {"capability": "file.preview", "arguments": {"path": outside.name, "workspace": str(tmp_path)}}))
    assert result["error"]["code"] == "outside_workspace"
    try:
        (workspace / "link.png").symlink_to(outside)
    except OSError:
        pytest.skip("File symlink creation requires local privileges")
    result = run(local.execute("capability_execute", {"capability": "file.preview", "arguments": {"path": "link.png"}}))
    assert result["error"]["code"] == "outside_workspace"


def test_remote_result_content_is_converted_at_hub(tmp_path):
    (tmp_path / "plot.png").write_bytes(PNG)
    agent = bound(tmp_path)

    class Remote:
        async def execute(self, tool, arguments, device=None):
            assert device == "agent-device"
            payload = await agent.execute(tool, arguments)
            return json.loads(json.dumps(payload))

    server = create_mcp_server(Remote())
    result = run(server.call_tool("capability_execute", {"capability": "file.preview", "arguments": {"path": "plot.png"}, "device": "agent-device"}))
    assert base64.b64decode(next(block for block in result.content if block.type == "image").data) == PNG


def test_preview_over_real_hub_agent_transport(tmp_path):
    from chat2local.agent.client import AgentClient
    from chat2local.app import create_app
    from test_devices import TOKEN, eventually, hub_runtime, running_agent, running_server
    from test_process_integration import mcp_client

    hub_root, agent_root = tmp_path / "hub", tmp_path / "agent"
    hub_root.mkdir()
    agent_root.mkdir()
    (agent_root / "plot.png").write_bytes(PNG)

    async def scenario():
        router = hub_runtime(hub_root)
        async with running_server(create_app(router, hub_token=TOKEN)) as (url, ws_url):
            async with running_agent(AgentClient(ws_url, "preview-agent", TOKEN, bound(agent_root))):
                await eventually(lambda: "preview-agent" in router.registry.sessions)
                async with mcp_client(url) as client:
                    result = await client.call_tool("capability_execute", {
                        "capability": "file.preview", "arguments": {"path": "plot.png"}, "device": "preview-agent",
                    })
                    assert not result.is_error
                    assert base64.b64decode(next(block for block in result.content if block.type == "image").data) == PNG
                    assert not (hub_root / "plot.png").exists()
    run(scenario())
