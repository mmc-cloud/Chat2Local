"""MCP Tool registration: the public surface exposed to the caller.

These tests cover the wiring rather than either Tool's algorithm: the public
schema is frozen, and the runtime ``AppConfig`` — not a Tool argument — decides
the limits the Tools run with.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from chat2local.app import create_app
from chat2local.device.registry import DeviceRegistry
from chat2local.dispatch.local import LocalToolDispatcher
from chat2local.hub.router import DeviceRouter
from chat2local.mcp import tools as mcp_tools
from chat2local.mcp.instructions import MCP_INSTRUCTIONS
from chat2local.mcp.server import mcp
from chat2local.runtime.config import AppConfig, ReadConfig, SearchConfig
from chat2local.dispatch import local as local_dispatch
from chat2local.runtime.workspace import WorkspaceManager

from conftest import run


@pytest.fixture
def server() -> MCPServer:
    registered = MCPServer("Test")
    mcp_tools.register(registered)

    return registered


@pytest.fixture
def bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A server whose workspace and config this test owns, reset afterwards."""

    root = tmp_path / "workspace"
    root.mkdir()

    def bind(config: AppConfig | None = None) -> Path:
        mcp_tools.configure_workspace(root)

        if config is not None:
            mcp_tools.configure_config(config)

        return root

    monkeypatch.setattr(mcp_tools, "_workspace", None)
    monkeypatch.setattr(mcp_tools, "_config", AppConfig())

    return bind


def call(server: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    """Call a Tool and unwrap the payload it returns."""

    return payload(run(server.call_tool(name, arguments)))


def payload(result: Any) -> dict[str, Any]:
    """The Tool's own return value, decoded from the MCP envelope."""

    import json

    return json.loads(result.content[0].text)


# --- registration --------------------------------------------------------------


def test_tools_are_registered_on_the_mcp_server(server: MCPServer) -> None:
    names = {tool.name for tool in run(server.list_tools())}

    assert names == {"read", "search", "apply_patch", "exec_command", "interact_process", "kill_process", "handoff_list", "handoff_get", "handoff_save", "list_devices"}


def test_standalone_and_hub_servers_share_mcp_instructions(tmp_path: Path) -> None:
    assert mcp.instructions == MCP_INSTRUCTIONS
    assert "AGENTS.md" in MCP_INSTRUCTIONS
    assert "handoff_list" in MCP_INSTRUCTIONS

    local = LocalToolDispatcher(WorkspaceManager(tmp_path), AppConfig())
    router = DeviceRouter("hub", local, DeviceRegistry("hub"))
    hub_app = create_app(router, hub_token="test-token")

    assert hub_app.state.mcp is not mcp
    assert hub_app.state.mcp.instructions == MCP_INSTRUCTIONS


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("read", {"path", "start_line", "end_line", "workspace", "device"}),
        ("apply_patch", {"patch", "workspace", "device"}),
        ("exec_command", {"command", "cwd", "workspace", "device"}),
        ("interact_process", {"process_id", "input", "device"}),
        ("kill_process", {"process_id", "device"}),
        ("handoff_list", {"workspace", "device"}),
        ("handoff_get", {"workstream", "workspace", "device"}),
        ("handoff_save", {"workstream", "title", "summary", "content", "expected_revision", "workspace", "device"}),
        (
            "search",
            {
                "query",
                "mode",
                "path",
                "include",
                "exclude",
                "regex",
                "case_sensitive",
                "max_results",
                "workspace",
                "device",
            },
        ),
    ],
)
def test_public_schemas_expose_exactly_the_frozen_parameters(
    server: MCPServer, name: str, expected: set[str]
) -> None:
    tool = next(tool for tool in run(server.list_tools()) if tool.name == name)

    assert set(tool.input_schema["properties"]) == expected


@pytest.mark.parametrize(
    "hidden",
    ["allowed_roots", "max_lines", "max_bytes", "timeout"],
)
def test_runtime_limits_are_not_tool_parameters(server: MCPServer, hidden: str) -> None:
    properties = {
        key
        for tool in run(server.list_tools())
        for key in tool.input_schema["properties"]
    }

    assert hidden not in properties


def test_required_parameters_are_unchanged(server: MCPServer) -> None:
    schemas = {tool.name: tool.input_schema for tool in run(server.list_tools())}

    assert schemas["read"]["required"] == ["path"]
    assert schemas["search"]["required"] == ["query", "mode"]
    assert schemas["apply_patch"]["required"] == ["patch"]
    assert schemas["exec_command"]["required"] == ["command"]
    assert schemas["interact_process"]["required"] == ["process_id"]
    assert schemas["kill_process"]["required"] == ["process_id"]
    assert schemas["handoff_get"]["required"] == ["workstream"]
    assert schemas["handoff_save"]["required"] == ["workstream", "title", "summary", "content", "expected_revision"]


# --- workspace binding ---------------------------------------------------------


def test_registered_read_tool_uses_configured_workspace(server: MCPServer, bound, tmp_path: Path) -> None:
    root = bound()
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")

    result = call(server, "read", {"path": "notes.txt"})

    assert "hello" in str(result)


def test_registered_tool_reports_tool_errors_readably(
    server: MCPServer, bound
) -> None:
    bound()

    with pytest.raises(ToolError) as failure:
        call(server, "read", {"path": "missing.txt"})

    assert "missing.txt" in str(failure.value)


def test_registered_tool_requires_a_workspace(server: MCPServer) -> None:
    mcp_tools._workspace = None

    with pytest.raises(ToolError) as failure:
        call(server, "read", {"path": "notes.txt"})

    assert "not configured" in str(failure.value)


def test_search_tool_rejects_a_path_outside_the_workspace(server: MCPServer, bound) -> None:
    bound()

    with pytest.raises(ToolError) as failure:
        call(server, "search", {"query": "x", "mode": "content", "path": "../outside"})

    assert "outside workspace" in str(failure.value)


# --- config drives the read limits ---------------------------------------------


def test_read_uses_configured_max_lines(server: MCPServer, bound) -> None:
    root = bound(AppConfig(read=ReadConfig(max_lines=1)))
    (root / "two.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    result = call(server, "read", {"path": "two.txt"})

    assert result["lines_returned"] == 1
    assert result["truncated"] is True
    assert result["truncation_reason"] == "line_limit"
    assert result["next_start_line"] == 2


def test_read_uses_configured_max_bytes(server: MCPServer, bound) -> None:
    root = bound(AppConfig(read=ReadConfig(max_bytes=8)))
    (root / "wide.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    result = call(server, "read", {"path": "wide.txt"})

    assert result["truncated"] is True
    assert result["truncation_reason"] == "byte_limit"
    assert len(result["text"].encode("utf-8")) <= 8


def test_read_keeps_default_limits_without_configuration(server: MCPServer, bound) -> None:
    root = bound()
    (root / "two.txt").write_text("alpha\nbeta\n", encoding="utf-8")

    result = call(server, "read", {"path": "two.txt"})

    assert result["lines_returned"] == 2
    assert result["truncated"] is False
    assert result["truncation_reason"] is None


# --- config drives the search limits -------------------------------------------


def searchable(root: Path) -> None:
    for index in range(5):
        (root / "pkg").mkdir(exist_ok=True)
        (root / "pkg" / f"module_{index}.py").write_text("needle\n", encoding="utf-8")


def test_search_uses_configured_max_results_when_the_caller_omits_it(
    server: MCPServer, bound
) -> None:
    root = bound(AppConfig(search=SearchConfig(max_results=2)))
    searchable(root)

    result = call(server, "search", {"query": "needle", "mode": "content"})

    assert result["result_count"] == 2
    assert result["truncation_reason"] == "max_results"


def test_search_tool_argument_overrides_the_configured_default(
    server: MCPServer, bound
) -> None:
    root = bound(AppConfig(search=SearchConfig(max_results=2)))
    searchable(root)

    result = call(server, "search", {"query": "needle", "mode": "content", "max_results": 4})

    assert result["result_count"] == 4
    assert result["truncation_reason"] == "max_results"


def test_search_tool_argument_still_honours_the_hard_cap(server: MCPServer, bound) -> None:
    """The public cap lives in search_impl, so a large Tool value is clamped there."""

    root = bound(AppConfig(search=SearchConfig(max_results=1)))
    for index in range(600):
        (root / "pkg").mkdir(exist_ok=True)
        (root / "pkg" / f"module_{index:03}.py").write_text("needle\n", encoding="utf-8")

    result = call(
        server, "search", {"query": "needle", "mode": "content", "max_results": 10_000}
    )

    assert result["result_count"] == 500
    assert result["truncation_reason"] == "max_results"


def test_search_uses_configured_timeout(server: MCPServer, bound, monkeypatch) -> None:
    root = bound(AppConfig(search=SearchConfig(timeout=0.25)))
    searchable(root)

    import chat2local.tools.search as search_module

    captured: dict[str, float] = {}
    real = search_module.search

    async def spy(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)

        return await real(*args, **kwargs)

    monkeypatch.setattr(local_dispatch, "search_impl", spy)

    call(server, "search", {"query": "needle", "mode": "content"})

    assert captured["timeout"] == 0.25


def test_search_never_forwards_a_caller_supplied_timeout(
    server: MCPServer, bound, monkeypatch
) -> None:
    """A caller cannot smuggle a timeout in: the runtime config is the only source."""

    root = bound(AppConfig(search=SearchConfig(timeout=0.25)))
    searchable(root)

    import chat2local.tools.search as search_module

    captured: dict[str, float] = {}
    real = search_module.search

    async def spy(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)

        return await real(*args, **kwargs)

    monkeypatch.setattr(local_dispatch, "search_impl", spy)

    call(server, "search", {"query": "needle", "mode": "content", "timeout": 99})

    assert captured["timeout"] == 0.25


# --- errors stay readable ------------------------------------------------------


def test_invalid_search_mode_becomes_a_tool_error(server: MCPServer, bound) -> None:
    bound()

    with pytest.raises(ToolError) as failure:
        call(server, "search", {"query": "needle", "mode": "path"})

    assert "mode must be" in str(failure.value)


def test_invalid_regex_becomes_a_tool_error(server: MCPServer, bound) -> None:
    bound()

    with pytest.raises(ToolError):
        call(server, "search", {"query": "needle[", "mode": "content", "regex": True})


def test_programmer_errors_are_not_swallowed(server: MCPServer, bound, monkeypatch) -> None:
    """A genuine bug is reported as a crash, not as a readable bad request.

    The Tool only translates the errors it expects; anything else reaches the
    server uncaught, which marks it unexpected and keeps the original as the
    cause instead of passing its text on as if the caller had asked badly.
    """

    from mcp.server.mcpserver.exceptions import UnexpectedToolError

    bound()

    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise KeyError("unexpected bug")

    monkeypatch.setattr(local_dispatch, "read_impl", boom)

    with pytest.raises(UnexpectedToolError) as failure:
        call(server, "read", {"path": "notes.txt"})

    assert "unexpected bug" not in str(failure.value)
    assert isinstance(failure.value.__cause__, KeyError)


@pytest.fixture
def allowed_workspaces(tmp_path: Path, bound) -> tuple[Path, Path, Path]:
    project_a = bound()
    project_b = tmp_path / "projectB"
    project_b.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project_a / "a.txt").write_text("needle A\n", encoding="utf-8", newline="")
    (project_b / "b.txt").write_text("needle B\n", encoding="utf-8", newline="")
    (outside / "secret.txt").write_text("needle secret\n", encoding="utf-8", newline="")
    mcp_tools.configure_workspace(project_a, allowed_roots=[project_a, project_b])

    return project_a, project_b, outside


@pytest.mark.parametrize("name", ["read", "search"])
def test_workspace_schema_and_description(server: MCPServer, name: str) -> None:
    tool = next(tool for tool in run(server.list_tools()) if tool.name == name)
    workspace = tool.input_schema["properties"]["workspace"]

    assert workspace["default"] is None
    assert {entry["type"] for entry in workspace["anyOf"]} == {"string", "null"}
    for phrase in (
        "absolute workspace path", "startup default", "locally configured allowed_roots",
        "only affects this Tool Call", "path itself is still bounded",
    ):
        assert phrase in tool.description


@pytest.mark.parametrize("name, extra", [
    ("read", {}), ("search", {"query": "needle", "mode": "name"}),
    ("search", {"query": "needle", "mode": "content"}),
])
def test_workspace_override_is_per_call(
    server: MCPServer, allowed_workspaces, name: str, extra: dict[str, Any]
) -> None:
    project_a, project_b, _ = allowed_workspaces
    # Match both file names in name mode, and file contents in content mode.
    if extra.get("mode") == "name":
        extra = {**extra, "query": ".txt"}
    default = mcp_tools.get_workspace()

    first = call(server, name, {**extra, "path": "a.txt" if name == "read" else "."})
    selected = call(server, name, {
        **extra, "path": "b.txt" if name == "read" else ".", "workspace": str(project_b),
    })
    again = call(server, name, {
        **extra, "path": "a.txt" if name == "read" else ".", "workspace": None,
    })

    if name == "read":
        assert first["text"] == again["text"] == "needle A\n"
        assert selected["text"] == "needle B\n"
        assert selected["path"] == "b.txt"
    else:
        assert {item["path"] for item in first["results"]} == {"a.txt"}
        assert {item["path"] for item in selected["results"]} == {"b.txt"}
        assert again["results"] == first["results"]
    assert mcp_tools.get_workspace() is default
    assert default.root == project_a.resolve()


@pytest.mark.parametrize("name, extra", [
    ("read", {}), ("search", {"query": "needle", "mode": "content"}),
])
@pytest.mark.parametrize("violation", ["outside", "relative", "parent", "absolute_path"])
def test_workspace_override_rejects_permission_escapes(
    server: MCPServer, allowed_workspaces, name: str, extra: dict[str, Any], violation: str
) -> None:
    project_a, project_b, outside = allowed_workspaces
    arguments = {**extra, "workspace": str(project_b), "path": "."}
    message = "outside workspace"
    if violation == "outside":
        arguments["workspace"] = str(outside)
        message = "outside allowed roots"
    elif violation == "relative":
        arguments["workspace"] = "projectB"
        message = "absolute path"
    elif violation == "parent":
        arguments["path"] = "../workspace/a.txt"
    else:
        arguments["path"] = str(project_a / "a.txt")

    with pytest.raises(ToolError, match=message):
        call(server, name, arguments)

    assert mcp_tools.get_workspace().root == project_a.resolve()


@pytest.mark.parametrize("name, extra", [
    ("read", {}), ("search", {"query": "needle", "mode": "content"}),
])
def test_workspace_override_can_select_a_sibling_under_one_allowed_root(
    server: MCPServer, allowed_workspaces, tmp_path: Path, name: str, extra: dict[str, Any]
) -> None:
    project_a, project_b, _ = allowed_workspaces
    mcp_tools.configure_workspace(project_a, allowed_roots=[tmp_path])

    result = call(server, name, {**extra, "workspace": str(project_b), "path": "b.txt"})
    assert "needle B" in str(result)
    with pytest.raises(ToolError, match="outside workspace"):
        call(server, name, {
            **extra, "workspace": str(project_b), "path": "../workspace/a.txt",
        })


@pytest.mark.parametrize("name, extra", [
    ("read", {}), ("search", {"query": "needle", "mode": "content"}),
])
def test_workspace_override_cannot_expand_implicit_allowed_roots(
    server: MCPServer, allowed_workspaces, name: str, extra: dict[str, Any]
) -> None:
    project_a, project_b, _ = allowed_workspaces
    mcp_tools.configure_workspace(project_a)

    with pytest.raises(ToolError, match="outside allowed roots"):
        call(server, name, {**extra, "workspace": str(project_b), "path": "b.txt"})
