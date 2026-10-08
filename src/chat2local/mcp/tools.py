"""MCP Tool registration for the Local Tools surface.

Design: docs/design-v0.1.md §4 Layer 1 — Local Tools.

Tools stay thin: they route to a device's ``LocalToolDispatcher``, which owns
its ``WorkspaceManager``, ``AppConfig`` and ``ProcessManager``. Limits such as
``max_lines`` / ``timeout`` are runtime configuration, never Tool parameters.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import socket
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import AfterValidator, Field, StrictInt, StrictStr

from mcp.server.mcpserver.exceptions import ToolError

from chat2local.mcp.metadata import TOOL_METADATA, annotations_for
from chat2local.mcp.output_schema import publish_output_schemas

from chat2local.runtime.config import AppConfig
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager
from chat2local.runtime.shell import ShellError
from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.device.registry import DeviceError
from chat2local.hub.router import DeviceRouter
from chat2local.handoff.store import (
    SUMMARY_MAX_LENGTH, TITLE_MAX_LENGTH, WORKSTREAM_PATTERN,
    validate_summary, validate_title, validate_workstream,
)

Workstream = Annotated[
    StrictStr, Field(json_schema_extra={"pattern": WORKSTREAM_PATTERN}),
    AfterValidator(validate_workstream),
]
ExpectedRevision = Annotated[StrictInt, Field(ge=0)]
HandoffTitle = Annotated[
    StrictStr, Field(min_length=1, max_length=TITLE_MAX_LENGTH), AfterValidator(validate_title),
]
HandoffSummary = Annotated[
    StrictStr, Field(min_length=1, max_length=SUMMARY_MAX_LENGTH), AfterValidator(validate_summary),
]

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

_workspace: WorkspaceManager | None = None
_config: AppConfig = AppConfig()
_router: DeviceRouter | None = None


def configure_workspace(
    path: str | Path,
    *,
    allowed_roots: Sequence[str | Path] | None = None,
) -> WorkspaceManager:
    """Bind the default workspace and the locally granted allowed roots."""

    global _workspace

    _workspace = WorkspaceManager(path, allowed_roots=allowed_roots)

    return _workspace


def get_workspace() -> WorkspaceManager:
    if _workspace is None:
        raise WorkspaceError("Workspace is not configured")

    return _workspace


def configure_config(config: AppConfig) -> AppConfig:
    """Bind the runtime configuration the Tools read their limits from."""

    global _config

    _config = config

    return _config


def get_config() -> AppConfig:
    return _config


def configure_router(router: DeviceRouter) -> None:
    global _router
    _router = router


def get_router() -> DeviceRouter:
    global _router
    if _router is None:
        _router = DeviceRouter(socket.gethostname(), LocalToolDispatcher(get_workspace(), get_config()))
    return _router


def register(server: MCPServer, router: DeviceRouter | None = None) -> None:
    """Register the ten public Tools on an MCP server."""

    @server.tool(name="read", title=TOOL_METADATA["read"]["title"],
                 description=TOOL_METADATA["read"]["description"],
                 annotations=annotations_for("read"))
    async def read_tool(
        path: Annotated[str, Field(description=TOOL_METADATA["read"]["params"]["path"])],
        start_line: Annotated[int | None, Field(description=TOOL_METADATA["read"]["params"]["start_line"])] = None,
        end_line: Annotated[int | None, Field(description=TOOL_METADATA["read"]["params"]["end_line"])] = None,
        workspace: Annotated[str | None, Field(description=TOOL_METADATA["read"]["params"]["workspace"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["read"]["params"]["device"])] = None,
    ) -> dict[str, Any]:

        try:
            return await (router or get_router()).execute("read", {
                "path": path, "start_line": start_line, "end_line": end_line,
                "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="search", title=TOOL_METADATA["search"]["title"],
                 description=TOOL_METADATA["search"]["description"],
                 annotations=annotations_for("search"))
    async def search_tool(
        query: Annotated[str, Field(description=TOOL_METADATA["search"]["params"]["query"])],
        mode: Annotated[str, Field(description=TOOL_METADATA["search"]["params"]["mode"])],
        path: Annotated[str, Field(description=TOOL_METADATA["search"]["params"]["path"])] = ".",
        include: Annotated[list[str] | None, Field(description=TOOL_METADATA["search"]["params"]["include"])] = None,
        exclude: Annotated[list[str] | None, Field(description=TOOL_METADATA["search"]["params"]["exclude"])] = None,
        regex: Annotated[bool, Field(description=TOOL_METADATA["search"]["params"]["regex"])] = False,
        case_sensitive: Annotated[bool, Field(description=TOOL_METADATA["search"]["params"]["case_sensitive"])] = False,
        max_results: Annotated[int | None, Field(description=TOOL_METADATA["search"]["params"]["max_results"])] = None,
        workspace: Annotated[str | None, Field(description=TOOL_METADATA["search"]["params"]["workspace"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["search"]["params"]["device"])] = None,
    ) -> dict[str, Any]:

        try:
            return await (router or get_router()).execute("search", {
                "query": query, "mode": mode, "path": path, "include": include,
                "exclude": exclude, "regex": regex, "case_sensitive": case_sensitive,
                "max_results": max_results, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="apply_patch", title=TOOL_METADATA["apply_patch"]["title"],
                 description=TOOL_METADATA["apply_patch"]["description"],
                 annotations=annotations_for("apply_patch"))
    async def apply_patch_tool(
        patch: Annotated[str, Field(description=TOOL_METADATA["apply_patch"]["params"]["patch"])],
        workspace: Annotated[str | None, Field(description=TOOL_METADATA["apply_patch"]["params"]["workspace"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["apply_patch"]["params"]["device"])] = None,
    ) -> dict[str, Any]:
        try:
            return await (router or get_router()).execute("apply_patch", {
                "patch": patch, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="exec_command", title=TOOL_METADATA["exec_command"]["title"],
                 description=TOOL_METADATA["exec_command"]["description"],
                 annotations=annotations_for("exec_command"))
    async def exec_command_tool(
        command: Annotated[str, Field(description=TOOL_METADATA["exec_command"]["params"]["command"])],
        cwd: Annotated[str, Field(description=TOOL_METADATA["exec_command"]["params"]["cwd"])] = ".",
        workspace: Annotated[str | None, Field(description=TOOL_METADATA["exec_command"]["params"]["workspace"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["exec_command"]["params"]["device"])] = None,
    ) -> dict[str, Any]:
        try:
            return await (router or get_router()).execute("exec_command", {
                "command": command, "cwd": cwd, "workspace": workspace,
            }, device)
        except (WorkspaceError, ShellError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="interact_process", title=TOOL_METADATA["interact_process"]["title"],
                 description=TOOL_METADATA["interact_process"]["description"],
                 annotations=annotations_for("interact_process"))
    async def interact_process_tool(
        process_id: Annotated[str, Field(description=TOOL_METADATA["interact_process"]["params"]["process_id"])],
        input: Annotated[str | None, Field(description=TOOL_METADATA["interact_process"]["params"]["input"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["interact_process"]["params"]["device"])] = None,
    ) -> dict[str, Any]:
        try:
            return await (router or get_router()).execute("interact_process", {
                "process_id": process_id, "input": input,
            }, device)
        except (WorkspaceError, ShellError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="kill_process", title=TOOL_METADATA["kill_process"]["title"],
                 description=TOOL_METADATA["kill_process"]["description"],
                 annotations=annotations_for("kill_process"))
    async def kill_process_tool(
        process_id: Annotated[str, Field(description=TOOL_METADATA["kill_process"]["params"]["process_id"])],
        device: Annotated[str | None, Field(description=TOOL_METADATA["kill_process"]["params"]["device"])] = None,
    ) -> dict[str, Any]:
        try:
            return await (router or get_router()).execute("kill_process", {
                "process_id": process_id,
            }, device)
        except (WorkspaceError, ShellError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="handoff_list", title=TOOL_METADATA["handoff_list"]["title"],
                 description=TOOL_METADATA["handoff_list"]["description"],
                 annotations=annotations_for("handoff_list"))
    async def handoff_list_tool(
        workspace: Annotated[str | None, Field(description=TOOL_METADATA["handoff_list"]["params"]["workspace"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["handoff_list"]["params"]["device"])] = None,
    ) -> dict[str, Any]:
        try:
            return await (router or get_router()).execute("handoff_list", {
                "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="handoff_get", title=TOOL_METADATA["handoff_get"]["title"],
                 description=TOOL_METADATA["handoff_get"]["description"],
                 annotations=annotations_for("handoff_get"))
    async def handoff_get_tool(
        workstream: Annotated[Workstream, Field(description=TOOL_METADATA["handoff_get"]["params"]["workstream"])],
        workspace: Annotated[str | None, Field(description=TOOL_METADATA["handoff_get"]["params"]["workspace"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["handoff_get"]["params"]["device"])] = None,
    ) -> dict[str, Any]:
        try:
            return await (router or get_router()).execute("handoff_get", {
                "workstream": workstream, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="handoff_save", title=TOOL_METADATA["handoff_save"]["title"],
                 description=TOOL_METADATA["handoff_save"]["description"],
                 annotations=annotations_for("handoff_save"))
    async def handoff_save_tool(
        workstream: Annotated[Workstream, Field(description=TOOL_METADATA["handoff_save"]["params"]["workstream"])],
        title: Annotated[HandoffTitle, Field(description=TOOL_METADATA["handoff_save"]["params"]["title"])],
        summary: Annotated[HandoffSummary, Field(description=TOOL_METADATA["handoff_save"]["params"]["summary"])],
        content: Annotated[StrictStr, Field(description=TOOL_METADATA["handoff_save"]["params"]["content"])],
        expected_revision: Annotated[ExpectedRevision, Field(description=TOOL_METADATA["handoff_save"]["params"]["expected_revision"])],
        workspace: Annotated[str | None, Field(description=TOOL_METADATA["handoff_save"]["params"]["workspace"])] = None,
        device: Annotated[str | None, Field(description=TOOL_METADATA["handoff_save"]["params"]["device"])] = None,
    ) -> dict[str, Any]:
        try:
            return await (router or get_router()).execute("handoff_save", {
                "workstream": workstream, "title": title, "summary": summary, "content": content,
                "expected_revision": expected_revision, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="list_devices", title=TOOL_METADATA["list_devices"]["title"],
                 description=TOOL_METADATA["list_devices"]["description"],
                 annotations=annotations_for("list_devices"))
    async def list_devices_tool() -> dict[str, Any]:
        try:
            return (router or get_router()).list_devices()
        except WorkspaceError as error:
            raise ToolError(str(error)) from error

    # Publish the existing result shapes without changing dict output conversion.
    publish_output_schemas(server)
