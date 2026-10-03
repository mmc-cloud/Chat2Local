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

    @server.tool(name="read")
    async def read_tool(
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        workspace: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Read a text file or list a single directory level inside the workspace.

        Args:
            path: File or directory path, relative to the workspace (or absolute inside it).
            start_line: First line to return, 1-based. Defaults to the beginning of the file.
            end_line: Last line to return, 1-based and inclusive. Defaults to the end of the file.
            workspace: Optional absolute workspace path. Omitted uses the startup default
                workspace. Must stay inside locally configured allowed_roots. Workspace
                selection only affects this Tool Call; path itself is still bounded to
                that workspace.
            device: Optional device_id. Omitted selects this server's local device.
                Remote devices are available only on a Hub; use list_devices to discover them.
                Workspace paths and permissions belong to the selected device.

        How much text comes back is capped by the configured read limits, not by these
        arguments. `truncated` says whether the result was cut short and
        `truncation_reason` says why: `line_limit` and `byte_limit` are ordinary and
        `next_start_line` gives the line to continue from. `line_too_long` means one
        line exceeded the whole budget, so only its prefix is returned and the
        remainder of that line cannot be read in V0.1. Binary files are reported as
        metadata without their content. Directories are listed one level only, never
        recursed.
        """

        try:
            return await (router or get_router()).execute("read", {
                "path": path, "start_line": start_line, "end_line": end_line,
                "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="search")
    async def search_tool(
        query: str,
        mode: str,
        path: str = ".",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int | None = None,
        workspace: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Search file names or file contents inside the workspace.

        Args:
            query: Text or pattern to search for.
            mode: "name" to match file names, "content" to match file contents.
            path: File or directory to search in, relative to the workspace.
            include: Glob patterns a path must match to be searched.
            exclude: Glob patterns that remove a path from the search.
            regex: Treat `query` as a regular expression instead of literal text.
            case_sensitive: Match case when true. Defaults to case-insensitive.
            max_results: Maximum number of results. Omit it to use the configured
                default; a value given here overrides that default. Never above 500.
            workspace: Optional absolute workspace path. Omitted uses the startup default
                workspace. Must stay inside locally configured allowed_roots. Workspace
                selection only affects this Tool Call; path itself is still bounded to
                that workspace.
            device: Optional device_id. Omitted selects this server's local device.
                Remote devices are available only on a Hub; use list_devices to discover them.
                Workspace paths and permissions belong to the selected device.

        How long the search may run is set by the configured runtime timeout, which is
        not a Tool argument. Results use workspace-relative paths. `truncated` says
        whether the search stopped early and `truncation_reason` says why:
        `max_results`, `timeout` or `output_limit`; `timed_out` is true only for
        `timeout`. Stopping early is a normal result, not an error. `.git` is always
        excluded, along with `node_modules`, `.venv`, `__pycache__`, `dist`, `build`
        and `coverage`; `.gitignore`, `.ignore` and `.rgignore` rules are honoured.
        """

        try:
            return await (router or get_router()).execute("search", {
                "query": query, "mode": mode, "path": path, "include": include,
                "exclude": exclude, "regex": regex, "case_sensitive": case_sensitive,
                "max_results": max_results, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="apply_patch")
    async def apply_patch_tool(
        patch: str,
        workspace: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Apply a strict Codex-style patch within one workspace on one device.

        Args:
            patch: Complete *** Begin Patch / *** End Patch text. Supports
                Add File (+lines), Update File (@@ exact context / -old / +new),
                Delete File, and Move Directory followed by *** Move to: path.
                Update File may include Move to, with or without content hunks.
                No fuzzy matching, ambiguous context, directory deletion or copy.
            workspace: Optional absolute workspace path. Omitted uses the startup
                default workspace on the selected device. Must stay inside its
                locally configured allowed_roots. Selection affects only this
                Tool Call; every patch path remains bounded to that workspace.
            device: Optional device_id. Omitted selects this server's local device.
                Use list_devices to discover remote Agents on a Hub.

        The whole patch is prepared before writing. Validation failures are Tool
        errors with no changes. Commit failures return success=false, completed
        operations and the failed step; partial changes are possible, with no
        rollback guarantee. Existing UTF-8 / BOM UTF-16 and newline styles are
        preserved; new files use UTF-8 without BOM and LF. Destinations must not
        exist. Operations cannot overlap paths. This tool has side effects:
        never automatically retry or replay a failed or timed-out call.
        """
        try:
            return await (router or get_router()).execute("apply_patch", {
                "patch": patch, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="exec_command")
    async def exec_command_tool(
        command: str,
        cwd: str = ".",
        workspace: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Execute a complete command with the selected device's configured Shell.

        Args:
            command: Complete Shell command, passed without parsing or splitting.
            cwd: Existing directory inside the selected workspace. Defaults to ".".
            workspace: Optional absolute workspace on the selected device. Omitted
                uses its startup default. Must stay inside locally configured
                allowed_roots; selection affects only this Tool Call. cwd remains
                bounded to that workspace.
            device: Optional device_id; omitted selects this server's local device.

        cwd restrictions are not an OS sandbox: commands have the Chat2Local OS
        user's permissions. Every result includes a managed process_id, state,
        exit_code, separate stdout/stderr, has_more and dropped flags, and draining.
        Nonzero exits and foreground timeouts are normal results. Timeout leaves
        the process running. Continue with interact_process; stop with kill_process.
        Always use the SAME device for later calls; process_id does not route devices.
        Exited processes with unread output remain readable during retention.
        This Tool has side effects. Remote timeout/disconnect does not guarantee
        cancellation: check the target's actual state before any retry. Never
        automatically retry or replay the command.
        """
        try:
            return await (router or get_router()).execute("exec_command", {
                "command": command, "cwd": cwd, "workspace": workspace,
            }, device)
        except (WorkspaceError, ShellError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="interact_process")
    async def interact_process_tool(
        process_id: str,
        input: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Read incremental stdout/stderr and optionally send exact UTF-8 stdin.

        Args:
            process_id: Chat2Local managed ID from exec_command, never an OS PID.
            input: Optional exact text to send. No newline is added; include "\\n"
                explicitly when needed. After writing, wait at most 250ms for output.
                Omitted input reads current output immediately without waiting.
            device: Same device used by exec_command. Omitted selects the local
                device; the Hub never guesses a process's owning device.

        Uses the same result shape and shared output budget as exec_command.
        stdout_has_more/stderr_has_more indicate unread retained output; dropped
        flags report output lost to bounded buffers. draining means pipe EOF is
        still pending, so a false has_more does not promise no future output.
        Exited/terminated records remain readable during retention; input to a
        closed stdin and unknown IDs are Tool errors. Input has side effects:
        check target state after remote timeout/disconnect; never replay blindly.
        """
        try:
            return await (router or get_router()).execute("interact_process", {
                "process_id": process_id, "input": input,
            }, device)
        except (WorkspaceError, ShellError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="kill_process")
    async def kill_process_tool(
        process_id: str,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Terminate a managed process tree on its owning device.

        Args:
            process_id: Chat2Local managed ID, never an arbitrary OS PID.
            device: Same device used by exec_command. Omitted selects the local
                device; the Hub never guesses process ownership.

        The device runtime attempts graceful termination then force after its
        configured grace period. Returns outcome=terminated or already_exited,
        plus the common process output fields. Killing consumes one output page;
        unread final output remains available through interact_process during
        retention. Already exited is a normal result; unknown_process is an error.
        Remote timeout/disconnect does not guarantee cancellation. Check target
        state before retry; never automatically retry or replay this side effect.
        """
        try:
            return await (router or get_router()).execute("kill_process", {
                "process_id": process_id,
            }, device)
        except (WorkspaceError, ShellError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="handoff_list")
    async def handoff_list_tool(
        workspace: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """List latest workstream metadata in the selected project's HANDOFFS/.

        workspace is an optional absolute directory inside the selected device's
        allowed_roots; omitted uses its startup default. device defaults to local.
        Returns sorted workstream/title/summary/revision/updated_at, without bodies.
        Use title and summary to discover and understand existing workstreams,
        then choose which full handoff to read. Legacy title/summary are null. Missing
        HANDOFFS returns an empty list without creating it. Malformed files fail
        with invalid_handoff; this tool never silently hides corrupted handoffs.
        """
        try:
            return await (router or get_router()).execute("handoff_list", {
                "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="handoff_get")
    async def handoff_get_tool(
        workstream: Workstream,
        workspace: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Read the latest workstream body, title/summary and revision/updated_at.

        workstream is a 1-80 character ASCII slug starting with a letter/digit,
        followed by letters, digits, '-' or '_', never a path. content excludes
        front matter. Missing files fail with handoff_not_found; damaged metadata
        fails with invalid_handoff. workspace is an optional absolute project
        directory inside the selected device's allowed_roots; omitted uses its
        startup default. device defaults to local; remote files live on the Agent.
        """
        try:
            return await (router or get_router()).execute("handoff_get", {
                "workstream": workstream, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="handoff_save")
    async def handoff_save_tool(
        workstream: Workstream,
        title: HandoffTitle,
        summary: HandoffSummary,
        content: StrictStr,
        expected_revision: ExpectedRevision,
        workspace: str | None = None,
        device: str | None = None,
    ) -> dict[str, Any]:
        """Atomically replace a workstream's latest Markdown body using its revision.

        workstream is an ASCII slug, never a file path. content is only the body;
        Chat2Local manages front matter and normalizes body line endings to LF.
        The calling model provides title (1-120 characters) and summary (1-500).
        Both are strict Unicode strings, single-line and nonblank; saved unchanged.
        Chat2Local only validates and persists them, never generates or summarizes
        semantic content. Title, summary and body update atomically as one state.
        expected_revision is a strict integer >= 0: use 0 to create, or the latest
        handoff_get revision to update. Success returns all metadata, without content.
        Reading legacy files returns null title/summary; the next save upgrades them.
        revision_conflict writes nothing: get the latest body, merge its meaning
        yourself, and save with that revision. No automatic merge or history.
        workspace selects a single absolute project inside the target device's
        allowed_roots; omitted uses its startup default. device defaults to local.
        Concurrent calls within one device runtime cannot silently overwrite.
        After timeout/disconnect, get the actual state before retrying; never replay
        automatically. Cross-process locking is outside V0.1's guarantee.
        """
        try:
            return await (router or get_router()).execute("handoff_save", {
                "workstream": workstream, "title": title, "summary": summary, "content": content,
                "expected_revision": expected_revision, "workspace": workspace,
            }, device)
        except (WorkspaceError, ToolExecutionError, DeviceError) as error:
            raise ToolError(str(error)) from error

    @server.tool(name="list_devices")
    async def list_devices_tool() -> dict[str, Any]:
        """List this server's local device and currently connected remote Agents.

        Returns device_id, kind (local/remote), online and tools. Disconnected Agents
        are removed; their device_id remains unavailable until they reconnect.
        Local filesystem permissions and allowed_roots are never included.
        """
        try:
            return (router or get_router()).list_devices()
        except WorkspaceError as error:
            raise ToolError(str(error)) from error
