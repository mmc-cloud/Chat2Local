"""Contract checks for English MCP metadata and published result schemas."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from chat2local.mcp.metadata import TOOL_METADATA
from chat2local.mcp.server import create_mcp_server
from chat2local.runtime.workspace import WorkspaceManager
from chat2local.tools.apply_patch import apply_patch
from chat2local.tools.read import read
from chat2local.tools.search import search

jsonschema = pytest.importorskip("jsonschema")


def listed():
    return asyncio.run(create_mcp_server().list_tools())


def test_all_public_tools_have_english_metadata_and_parameter_descriptions() -> None:
    registered = {tool.name: tool for tool in listed()}
    assert set(registered) == set(TOOL_METADATA)
    assert len(registered) == 10

    for name, tool in registered.items():
        assert tool.title == TOOL_METADATA[name]["title"]
        assert tool.description == TOOL_METADATA[name]["description"]
        assert tool.description
        assert tool.annotations is not None
        for parameter, details in tool.input_schema["properties"].items():
            assert details["description"] == TOOL_METADATA[name]["params"][parameter]
            assert details["description"]

        expected = TOOL_METADATA[name]["annotations"]
        assert tool.annotations.model_dump(by_alias=True, exclude_none=True) == expected


def test_all_published_output_schemas_are_valid_json_schema() -> None:
    for tool in listed():
        assert tool.output_schema is not None
        assert tool.output_schema["type"] == "object"
        jsonschema.Draft202012Validator.check_schema(tool.output_schema)


def test_read_output_schema_all_three_result_variants(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path)
    (tmp_path / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\xff")
    schemas = {tool.name: tool.output_schema for tool in listed()}
    for path in ["notes.txt", ".", "binary.bin"]:
        result = asyncio.run(read(workspace, path))
        jsonschema.validate(result, schemas["read"])

    assert len(schemas["read"]["oneOf"]) == 3


def test_search_output_schema_both_modes(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path)
    (tmp_path / "item.txt").write_text("needle\n", encoding="utf-8")
    schema = {tool.name: tool.output_schema for tool in listed()}["search"]
    for mode in ("name", "content"):
        result = asyncio.run(search(workspace, "item" if mode == "name" else "needle", mode=mode))
        jsonschema.validate(result, schema)
    assert len(schema["oneOf"]) == 2


def test_apply_patch_output_schema_success_and_partial_failure(tmp_path: Path) -> None:
    workspace = WorkspaceManager(tmp_path)
    result = asyncio.run(apply_patch(workspace, "*** Begin Patch\n*** Add File: new.txt\n+hello\n*** End Patch"))
    schema = {tool.name: tool.output_schema for tool in listed()}["apply_patch"]
    jsonschema.validate(result, schema)

    partial = {
        "success": False, "partial": True,
        "operations": [{"action": "add", "path": "new.txt"}],
        "created_directories": ["subfolder"],
        "temporary_files": [],
        "failed_operation": {"action": "delete", "path": "missing.txt"},
        "error": "Commit failed",
    }
    jsonschema.validate(partial, schema)


def test_process_and_handoff_output_structures() -> None:
    schemas = {tool.name: tool.output_schema for tool in listed()}
    process = {
        "process_id": "managed-id", "state": "exited", "exit_code": 0, "draining": False,
        "stdout": "hello", "stderr": "", "stdout_has_more": False,
        "stderr_has_more": False, "stdout_dropped": False, "stderr_dropped": False,
    }
    for name in ("exec_command", "interact_process"):
        jsonschema.validate(process, schemas[name])
    jsonschema.validate({**process, "outcome": "already_exited"}, schemas["kill_process"])
    record = {
        "workstream": "feature", "revision": 1, "updated_at": "2026-01-01T00:00:00Z",
        "title": "Feature", "summary": "Continuing work",
    }
    jsonschema.validate(record, schemas["handoff_save"])
    jsonschema.validate({**record, "content": "Next step"}, schemas["handoff_get"])
    jsonschema.validate({"handoffs": [record]}, schemas["handoff_list"])
    jsonschema.validate({"devices": [{
        "device_id": "host", "kind": "local", "online": True, "tools": ["read"],
    }]}, schemas["list_devices"])


def test_input_contract_still_exposes_original_defaults_and_return_conversion() -> None:
    registered = {tool.name: tool for tool in listed()}
    assert registered["read"].input_schema["required"] == ["path"]
    assert registered["search"].input_schema["required"] == ["query", "mode"]
    assert registered["search"].input_schema["properties"]["max_results"]["default"] is None
    assert registered["exec_command"].input_schema["properties"]["cwd"]["default"] == "."
    assert registered["handoff_save"].input_schema["required"] == [
        "workstream", "title", "summary", "content", "expected_revision",
    ]
