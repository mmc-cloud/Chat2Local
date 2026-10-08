"""Published JSON Schemas for Chat2Local's existing structured results.

The SDK retains the original dict[str, Any] output model. These schemas only
describe the existing result shapes; no new result serialization is introduced.
"""

from __future__ import annotations

from typing import Any


def field(type_: str | list[str], description: str, **extra: Any) -> dict[str, Any]:
    return {"type": type_, "description": description, **extra}


def nullable(type_: str, description: str, **extra: Any) -> dict[str, Any]:
    return field([type_, "null"], description, **extra)


def obj(fields: dict[str, Any], *, required: list[str] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "object", "properties": fields, "additionalProperties": False,
    }
    if required is not None:
        result["required"] = required
    return result


def array(items: dict[str, Any], description: str) -> dict[str, Any]:
    return {"type": "array", "items": items, "description": description}


def read_schema() -> dict[str, Any]:
    file_fields = {
        "kind": field("string", "Text file result.", const="file"),
        "path": field("string", "Workspace-relative file path."),
        "encoding": field("string", "Detected file text encoding."),
        "size": field("integer", "File size in bytes.", minimum=0),
        "start_line": nullable("integer", "First returned 1-based line, if any."),
        "end_line": nullable("integer", "Last returned 1-based line, if any."),
        "lines_returned": field("integer", "Number of returned lines.", minimum=0),
        "text": field("string", "Content returned within the configured limits."),
        "truncated": field("boolean", "Whether file content was cut short."),
        "truncation_reason": nullable("string", "line_limit, byte_limit, line_too_long, or null.",
                                      enum=["line_limit", "byte_limit", "line_too_long", None]),
        "line_truncated": field("boolean", "Whether an oversized line was only partially returned."),
        "next_start_line": nullable("integer", "Next line for continuation; cannot recover partial-line remainder."),
        "total_lines": nullable("integer", "Total line count, if known."),
    }
    entry = {
        "name": field("string", "Directory entry name."),
        "type": field("string", "Entry type.", enum=["file", "dir", "symlink", "other"]),
        "size": field("integer", "Size in bytes, present only for regular files.", minimum=0),
    }
    directory_fields = {
        "kind": field("string", "Directory listing result.", const="directory"),
        "path": field("string", "Workspace-relative directory path."),
        "entries": array(obj(entry, required=["name", "type"]), "Direct children; nonrecursive."),
        "entry_count": field("integer", "Number of returned entries.", minimum=0),
        "truncated": field("boolean", "Whether listing was cut short."),
        "truncation_reason": nullable("string", "byte_limit, scan_limit, or null.",
                                      enum=["byte_limit", "scan_limit", None]),
    }
    binary_fields = {
        "kind": field("string", "Binary metadata result.", const="binary"),
        "path": field("string", "Workspace-relative binary file path."),
        "size": field("integer", "File size in bytes.", minimum=0),
        "message": field("string", "Binary content is not returned."),
        "truncation_reason": field("null", "No text truncation applies.", const=None),
    }
    return {
        "type": "object",
        "oneOf": [
            obj(file_fields, required=list(file_fields)),
            obj(directory_fields, required=list(directory_fields)),
            obj(binary_fields, required=list(binary_fields)),
        ],
        "description": "A text file, directory listing, or binary-file metadata.",
    }


def search_schema() -> dict[str, Any]:
    base = {
        "query": field("string", "Search query as supplied."),
        "path": field("string", "Workspace-relative search root."),
        "engine": field("string", "Search backend.", enum=["ripgrep", "python"]),
        "result_count": field("integer", "Number of returned matches.", minimum=0),
        "truncated": field("boolean", "Whether the search ended before full completion."),
        "truncation_reason": nullable("string", "Early termination reason.",
                                      enum=["max_results", "timeout", "output_limit", None]),
        "timed_out": field("boolean", "True only for timeout truncation."),
    }
    name_item = obj({
        "path": field("string", "Workspace-relative matching file path."),
        "name": field("string", "Matching file basename."),
        "type": field("string", "File result type.", const="file"),
    }, required=["path", "name", "type"])
    content_item = obj({
        "path": field("string", "Workspace-relative source file."),
        "line": field("integer", "1-based matching line number."),
        "text": field("string", "Matching line or a shortened matching excerpt."),
        "text_truncated": field("boolean", "Present only if the text was shortened.", const=True),
    }, required=["path", "line", "text"])
    return {
        "type": "object",
        "oneOf": [
            obj({**base, "mode": field("string", "File name matching mode.", const="name"),
                 "results": array(name_item, "Matching file names.")},
                required=[*base, "mode", "results"]),
            obj({**base, "mode": field("string", "File content matching mode.", const="content"),
                 "results": array(content_item, "Matching text lines.")},
                required=[*base, "mode", "results"]),
        ],
        "description": "Filename or file-content search results.",
    }


def process_fields() -> dict[str, Any]:
    return {
        "process_id": field("string", "Device-local Chat2Local-managed process ID, not an OS PID."),
        "state": field("string", "Managed process state.", enum=["running", "exited", "terminated"]),
        "exit_code": nullable("integer", "Exit status if available; nonzero is not a Tool Error."),
        "draining": field("boolean", "Whether stdout/stderr pipes are still being drained."),
        "stdout": field("string", "Newly consumed retained stdout."),
        "stderr": field("string", "Newly consumed retained stderr."),
        "stdout_has_more": field("boolean", "Whether currently retained unread stdout remains."),
        "stderr_has_more": field("boolean", "Whether currently retained unread stderr remains."),
        "stdout_dropped": field("boolean", "Whether older stdout was lost to buffer limits."),
        "stderr_dropped": field("boolean", "Whether older stderr was lost to buffer limits."),
    }


def handoff_fields() -> dict[str, Any]:
    return {
        "workstream": field("string", "Stable workstream slug."),
        "revision": field("integer", "Latest revision.", minimum=1),
        "updated_at": field("string", "UTC last-updated timestamp."),
        "title": field("string", "Workstream title."),
        "summary": field("string", "Current-state summary."),
    }


def patch_step() -> dict[str, Any]:
    return obj({
        "action": field("string", "Performed or failed patch action."),
        "path": field("string", "Workspace-relative source path."),
        "destination": field("string", "Workspace-relative move destination if applicable."),
        "phase": field("string", "Cleanup failure phase if applicable."),
    }, required=["action", "path"])


def build_output_schemas() -> dict[str, dict[str, Any]]:
    common = process_fields()
    handoff = handoff_fields()
    steps = {
        "success": field("boolean", "True only when the patch fully commits."),
        "partial": field("boolean", "Whether partial changes or created artifacts remain."),
        "operations": array(patch_step(), "Successfully completed patch operations."),
        "created_directories": array(field("string", "Workspace-relative new directory."),
                                     "Parent directories created."),
        "temporary_files": array(field("string", "Workspace-relative temporary file."),
                                 "Temporary files left by a failed cleanup."),
        "failed_operation": {
            "anyOf": [patch_step(), {"type": "null"}],
            "description": "Failed patch operation or null.",
        },
        "error": nullable("string", "Commit failure message, or null on success."),
    }
    device = {
        "device_id": field("string", "Device routing ID."),
        "kind": field("string", "Local server or remote agent.", enum=["local", "remote"]),
        "online": field("boolean", "Current connection status."),
        "tools": array(field("string", "Supported tool name."), "Declared tool capabilities."),
    }
    return {
        "read": read_schema(),
        "search": search_schema(),
        "list_devices": obj({"devices": array(obj(device, required=list(device)),
                                               "Local and connected remote devices.")},
                            required=["devices"]),
        "apply_patch": obj(steps, required=list(steps)),
        "exec_command": obj(common, required=list(common)),
        "interact_process": obj(common, required=list(common)),
        "kill_process": obj({**common, "outcome": field("string", "Termination result.",
                                                       enum=["terminated", "already_exited"])},
                            required=[*common, "outcome"]),
        "handoff_list": obj({"handoffs": array(obj(handoff, required=list(handoff)),
                                                "Latest workstream metadata without the body.")},
                            required=["handoffs"]),
        "handoff_get": obj({**handoff, "content": field("string", "Continuation body without front matter.")},
                           required=[*handoff, "content"]),
        "handoff_save": obj(handoff, required=list(handoff)),
    }


OUTPUT_SCHEMAS = build_output_schemas()


def publish_output_schemas(server: Any) -> None:
    """Publish schemas without changing the original output validation model.

    The installed MCP SDK has no public output_schema decorator argument.
    FuncMetadata explicitly allows its published schema to be replaced live;
    the existing dict[str, Any] output_model still converts responses as before.
    """
    for name, schema in OUTPUT_SCHEMAS.items():
        tool = server._tool_manager.get_tool(name)
        if tool is None:
            raise RuntimeError(f"Missing registered MCP Tool: {name}")
        tool.fn_metadata.output_schema = schema
