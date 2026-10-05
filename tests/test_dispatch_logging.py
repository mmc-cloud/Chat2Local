"""Metadata-only central Tool lifecycle logs without changing error semantics."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from chat2local.dispatch import local as local_module
from chat2local.dispatch.local import ToolExecutionError
from chat2local.handoff.models import HandoffError
from chat2local.runtime.config import validation_message
from chat2local.runtime.process_manager import ProcessError, UnknownProcessError
from chat2local.runtime.shell import ShellError
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager
from chat2local.tools.apply_patch import PatchError
from chat2local.tools.read import ReadError
from chat2local.tools.search import SearchError
from conftest import run
from test_dispatch import make_dispatcher


def fixed_duration(monkeypatch):
    ticks = iter((8.0, 8.25))
    monkeypatch.setattr(local_module, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))


def dispatch_records(caplog):
    return [record for record in caplog.records if record.name == "chat2local.dispatch.local"]


def test_success_logs_started_and_duration_without_changing_result(tmp_path, monkeypatch, caplog):
    dispatcher = make_dispatcher(tmp_path)
    arguments = {"command": "PRIVATE-COMMAND"}
    result = {"stdout": "PRIVATE-RESULT"}
    async def handler(received):
        assert received is arguments
        return result
    dispatcher._handlers["exec_command"] = handler
    fixed_duration(monkeypatch)
    caplog.set_level(logging.DEBUG, logger="chat2local.dispatch.local")
    assert run(dispatcher.execute("exec_command", arguments)) is result
    records = dispatch_records(caplog)
    assert [(record.levelno, record.getMessage()) for record in records] == [
        (logging.DEBUG, "Tool exec_command started"),
        (logging.INFO, "Tool exec_command succeeded duration_ms=250"),
    ]
    assert "PRIVATE-COMMAND" not in caplog.text and "PRIVATE-RESULT" not in caplog.text


def test_validation_failure_preserves_message_and_suppressed_chaining(tmp_path, monkeypatch, caplog):
    arguments = {"path": {"secret": "PRIVATE-INPUT"}}
    with pytest.raises(local_module.ValidationError) as validation:
        local_module.ReadArguments.model_validate(arguments)
    fixed_duration(monkeypatch)
    with pytest.raises(ToolExecutionError) as failure:
        run(make_dispatcher(tmp_path).execute("read", arguments))
    assert str(failure.value) == f"Invalid arguments for read: {validation_message(validation.value)}"
    assert failure.value.__cause__ is None and failure.value.__suppress_context__ is True
    records = dispatch_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert records[0].getMessage() == "Tool read failed error_type=ValidationError duration_ms=250"
    assert records[0].exc_info is None and "PRIVATE-INPUT" not in caplog.text


@pytest.mark.parametrize("kind", [
    UnknownProcessError, ToolExecutionError, WorkspaceError, ShellError, HandoffError,
    PatchError, ReadError, SearchError, OSError, ProcessError,
])
def test_expected_failure_logs_only_type_and_preserves_normalization(tmp_path, monkeypatch, caplog, kind):
    dispatcher = make_dispatcher(tmp_path)
    error = kind("PRIVATE-ERROR-BODY")
    async def handler(arguments):
        raise error
    dispatcher._handlers["read"] = handler
    fixed_duration(monkeypatch)
    with pytest.raises(ToolExecutionError) as failure:
        run(dispatcher.execute("read", {"path": "PRIVATE-PATH"}))
    expected = "unknown_process: Unknown managed process_id" if kind is UnknownProcessError else str(error)
    assert str(failure.value) == expected
    assert failure.value.__cause__ is error
    records = dispatch_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.WARNING
    assert records[0].getMessage() == f"Tool read failed error_type={kind.__name__} duration_ms=250"
    assert records[0].exc_info is None
    assert "PRIVATE-ERROR-BODY" not in caplog.text and "PRIVATE-PATH" not in caplog.text


def test_unexpected_failure_has_one_traceback_and_preserves_outward_error(tmp_path, caplog):
    dispatcher = make_dispatcher(tmp_path)
    error = RuntimeError("internal bug")
    async def handler(arguments):
        raise error
    dispatcher._handlers["read"] = handler
    with pytest.raises(ToolExecutionError) as failure:
        run(dispatcher.execute("read", {}))
    assert str(failure.value) == "Internal local tool error"
    assert failure.value.__cause__ is None and failure.value.__suppress_context__ is True
    records = dispatch_records(caplog)
    assert len(records) == 1 and records[0].levelno == logging.ERROR
    assert records[0].getMessage() == "Unexpected local tool failure"
    assert records[0].exc_info[1] is error
    assert caplog.text.count("Traceback (most recent call last)") == 1


@pytest.mark.parametrize("tool,arguments", [
    ("exec_command", {"command": "UNCONFIGURED-SECRET"}),
    ("apply_patch", {"patch": "UNCONFIGURED-SECRET"}),
    ("interact_process", {"process_id": "private", "input": "UNCONFIGURED-SECRET"}),
    ("handoff_save", {"content": "UNCONFIGURED-SECRET"}),
    ("read", {"path": "UNCONFIGURED-SECRET"}),
    ("search", {"query": "UNCONFIGURED-SECRET"}),
])
@pytest.mark.parametrize("failed", [False, True])
def test_arguments_results_and_expected_error_bodies_never_reach_either_sink(
    tmp_path, isolated_user_data, configure_test_logging, capsys, tool, arguments, failed,
):
    configure_test_logging(debug=True)  # Deliberately no configured secrets.
    dispatcher = make_dispatcher(tmp_path)
    result = {"content": "UNCONFIGURED-SECRET", "stdout": "UNCONFIGURED-SECRET"}
    async def handler(received):
        assert received is arguments
        if failed:
            raise ToolExecutionError("UNCONFIGURED-SECRET")
        return result
    dispatcher._handlers[tool] = handler
    if failed:
        with pytest.raises(ToolExecutionError, match="UNCONFIGURED-SECRET"):
            run(dispatcher.execute(tool, arguments))
    else:
        assert run(dispatcher.execute(tool, arguments)) is result
    file_text = (isolated_user_data / "logs" / "chat2local.log").read_text(encoding="utf-8")
    for text in (capsys.readouterr().err, file_text):
        assert f"Tool {tool} started" in text
        assert f"Tool {tool} {'failed' if failed else 'succeeded'}" in text
        assert "duration_ms=" in text
        assert "UNCONFIGURED-SECRET" not in text
        assert len(text.splitlines()) == 2


def test_unknown_tool_is_not_logged_or_injected(tmp_path, caplog):
    name = "unknown\nINJECTED-EVENT"
    with pytest.raises(ToolExecutionError) as failure:
        run(make_dispatcher(tmp_path).execute(name, {}))
    assert str(failure.value) == f"Unknown local tool: {name}"
    assert dispatch_records(caplog) == []


def test_cancellation_still_propagates_without_success_or_failure(tmp_path, caplog):
    dispatcher = make_dispatcher(tmp_path)
    async def handler(arguments):
        raise asyncio.CancelledError
    dispatcher._handlers["read"] = handler
    with pytest.raises(asyncio.CancelledError):
        run(dispatcher.execute("read", {}))
    assert dispatch_records(caplog) == []


def test_multiple_workspaces_use_one_core_log(
    tmp_path, isolated_user_data, configure_test_logging, capsys,
):
    roots = [tmp_path / "A", tmp_path / "B"]
    for root in roots:
        root.mkdir()
        (root / "file.txt").write_text("PRIVATE-FILE-CONTENT", encoding="utf-8")
    dispatcher = local_module.LocalToolDispatcher(
        WorkspaceManager(roots[0], allowed_roots=roots), local_module.AppConfig(),
    )
    configure_test_logging()
    for root in roots:
        result = run(dispatcher.execute("read", {"path": "file.txt", "workspace": str(root)}))
        assert "PRIVATE-FILE-CONTENT" in str(result)
    logs = list(tmp_path.rglob("chat2local.log*"))
    assert logs == [isolated_user_data / "logs" / "chat2local.log"]
    assert all({path.name for path in root.iterdir()} == {"file.txt"} for root in roots)
    for text in (logs[0].read_text(encoding="utf-8"), capsys.readouterr().err):
        assert text.count("Tool read succeeded duration_ms=") == 2
        assert "PRIVATE-FILE-CONTENT" not in text and str(tmp_path) not in text
