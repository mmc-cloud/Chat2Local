"""Process Tool result mapping and device-local dispatcher integration."""

import asyncio
from contextlib import asynccontextmanager
from time import monotonic
from uuid import UUID

import pytest
from chat2local.mcp.server import create_mcp_server
from mcp.server.mcpserver.exceptions import ToolError

from chat2local.dispatch.local import LocalToolDispatcher, ToolExecutionError
from chat2local.hub.router import DeviceRouter
from chat2local.mcp import tools as mcp_tools
from chat2local.runtime.config import AppConfig, ProcessConfig
from chat2local.runtime.process_manager import ProcessError, ProcessManager
from chat2local.runtime.shell import ResolvedShell
from chat2local.runtime.workspace import WorkspaceManager
from chat2local.tools.interact_process import interact_process
from conftest import run
from test_apply_patch import create_link
from test_process_manager import PYTHON, eventually

OUTPUT_FIELDS = {
    "process_id", "state", "exit_code", "draining", "stdout", "stderr",
    "stdout_has_more", "stderr_has_more", "stdout_dropped", "stderr_dropped",
}
LOCAL_TOOLS = ("read", "search", "apply_patch", "exec_command", "interact_process", "kill_process", "handoff_list", "handoff_get", "handoff_save")


@asynccontextmanager
async def dispatched(root, *, config=None, workspace=None, shell=PYTHON):
    config = config or ProcessConfig(foreground_timeout=0.08, terminate_grace_period=0.05)
    manager = ProcessManager(config, shell=shell)
    dispatcher = LocalToolDispatcher(workspace or WorkspaceManager(root), AppConfig(process=config), manager)
    try:
        yield dispatcher
    finally:
        await manager.shutdown()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_short_command_streams_unicode_nonzero_and_identity(tmp_path, exit_code):
    async def scenario():
        async with dispatched(tmp_path, config=ProcessConfig(foreground_timeout=5)) as local:
            output = await local.execute("exec_command", {"command": (
                "import sys; sys.stdout.write('中文😀'); sys.stderr.write('错误'); "
                f"sys.exit({exit_code})"
            )})
            assert set(output) == OUTPUT_FIELDS
            assert output["state"] == "exited" and output["exit_code"] == exit_code
            assert output["stdout"] == "中文😀" and output["stderr"] == "错误"
            assert not output["draining"] and not output["stdout_has_more"] and not output["stderr_has_more"]
            assert UUID(output["process_id"]).version == 4
            record = local.process_manager.records[output["process_id"]]
            assert str(record.process.pid) != output["process_id"]
            assert "pid" not in output and "cwd" not in output and str(tmp_path) not in str(output)
            assert (await local.execute("interact_process", {"process_id": output["process_id"]}))["stdout"] == ""
    run(scenario())


def test_cwd_override_and_default_are_call_local(tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    for p in (a, b, b / "sub"):
        p.mkdir()
        (p / "marker").write_text(p.name, encoding="utf-8")
    workspace = WorkspaceManager(a, allowed_roots=[tmp_path])
    command = "from pathlib import Path; print(Path('marker').read_text())"
    async def scenario():
        async with dispatched(a, workspace=workspace, config=ProcessConfig(foreground_timeout=5)) as local:
            selected = await local.execute("exec_command", {"command": command, "workspace": str(b), "cwd": "sub"})
            assert selected["stdout"].strip() == "sub"
            default = await local.execute("exec_command", {"command": command})
            assert default["stdout"].strip() == "A" and local.workspace is workspace
    run(scenario())


@pytest.mark.parametrize("arguments,match", [
    ({"cwd": "../B"}, "outside workspace"),
    ({"cwd": "missing"}, "existing directory"),
    ({"cwd": "file"}, "existing directory"),
    ({"workspace": "B"}, "absolute path"),
])
def test_invalid_workspace_and_cwd_never_spawn(tmp_path, arguments, match):
    a = tmp_path / "A"
    a.mkdir()
    (tmp_path / "B").mkdir()
    (a / "file").write_text("x")
    async def scenario():
        async with dispatched(a) as local:
            with pytest.raises(ToolExecutionError, match=match):
                await local.execute("exec_command", {"command": "raise AssertionError", **arguments})
            assert not local.process_manager.records
    run(scenario())


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_cwd_resolve_escape(tmp_path, kind):
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    create_link(root / "link", outside, kind)
    async def scenario():
        async with dispatched(root) as local:
            for arguments, match in (
                ({"cwd": str(outside)}, "outside workspace"),
                ({"cwd": "link"}, "outside workspace"),
                ({"workspace": str(outside)}, "outside allowed roots"),
            ):
                with pytest.raises(ToolExecutionError, match=match):
                    await local.execute("exec_command", {"command": "pass", **arguments})
            assert not local.process_manager.records
    run(scenario())


def test_foreground_timeout_does_not_kill_and_incremental_streams(tmp_path):
    async def scenario():
        async with dispatched(tmp_path) as local:
            output = await local.execute("exec_command", {"command": (
                "import sys; sys.stdout.write('first'); sys.stdout.flush(); "
                "sys.stderr.write('error'); sys.stderr.flush(); "
                "sys.stdin.buffer.read(1); sys.stdout.write('second'); sys.stdout.flush()"
            )})
            pid = output["process_id"]
            assert output["state"] == "running" and output["exit_code"] is None
            record = local.process_manager.records[pid]
            await eventually(lambda: record.stdout.retained_end >= 5 and record.stderr.retained_end >= 5)
            started = monotonic()
            again = await local.execute("interact_process", {"process_id": pid})
            assert monotonic() - started < 0.1
            assert output["stdout"] + again["stdout"] == "first"
            assert output["stderr"] + again["stderr"] == "error"
            assert (await local.execute("interact_process", {"process_id": pid}))["stdout"] == ""
            final = await local.execute("interact_process", {"process_id": pid, "input": "x"})
            await local.process_manager.wait(pid, timeout=5)
            tail = await local.execute("interact_process", {"process_id": pid})
            assert final["stdout"] + tail["stdout"] == "second"
            assert tail["state"] == "exited"
    run(scenario())


@pytest.mark.parametrize("text", ["yes", "yes\n", "中文😀"])
def test_exact_input_without_automatic_newline(tmp_path, text):
    data = text.encode("utf-8")
    async def scenario():
        async with dispatched(tmp_path) as local:
            result = await local.execute("exec_command", {"command": (
                f"import sys; data=sys.stdin.buffer.read({len(data)}); "
                "sys.stdout.buffer.write(data); sys.stdout.flush(); sys.stdin.buffer.read(1)"
            )})
            pid = result["process_id"]
            sent = await local.execute("interact_process", {"process_id": pid, "input": text})
            assert sent["stdout"] == text and sent["state"] == "running"
            # An automatic extra newline would have released the second read.
            await asyncio.sleep(0.03)
            assert local.process_manager.status(pid).state == "running"
            await local.process_manager.close_stdin(pid)
            await local.process_manager.wait(pid, timeout=5)
    run(scenario())


def test_input_wait_bound_and_no_response_is_normal(tmp_path, monkeypatch):
    from chat2local.tools import interact_process as module
    actual_sleep = asyncio.sleep
    sleeps = []
    async def observe(delay):
        sleeps.append(delay)
        await actual_sleep(delay)
    async def scenario():
        async with dispatched(tmp_path) as local:
            result = await local.execute("exec_command", {"command": "import time; time.sleep(60)"})
            # Patch only around interaction, leaving manager setup unaffected.
            monkeypatch.setattr(module.asyncio, "sleep", observe)
            start = monotonic()
            output = await interact_process(local.process_manager, result["process_id"], input="x")
            elapsed = monotonic() - start
            assert sleeps == [0.250] and elapsed < 0.45
            assert output["state"] == "running" and output["stdout"] == output["stderr"] == ""
            monkeypatch.setattr(module.asyncio, "sleep", actual_sleep)
    run(scenario())


@pytest.mark.parametrize("limit", [4, 32768])
def test_fast_exit_large_unicode_output_paginated_without_loss(tmp_path, limit):
    expected_out, expected_err = "😀" * 9000, "错误" * 6000
    async def scenario():
        config = ProcessConfig(foreground_timeout=5, response_output_limit=limit)
        async with dispatched(tmp_path, config=config) as local:
            output = await local.execute("exec_command", {"command": (
                "import sys; sys.stdout.write('😀'*9000); sys.stderr.write('错误'*6000)"
            )})
            assert output["state"] == "exited" and output["process_id"]
            assert output["stdout_has_more"] or output["stderr_has_more"]
            pages = []
            while True:
                assert len(output["stdout"].encode()) + len(output["stderr"].encode()) <= limit
                assert not output["stdout_dropped"] and not output["stderr_dropped"]
                pages.append(output)
                if not output["stdout_has_more"] and not output["stderr_has_more"]:
                    break
                output = await local.execute("interact_process", {"process_id": output["process_id"]})
            assert "".join(p["stdout"] for p in pages) == expected_out
            assert "".join(p["stderr"] for p in pages) == expected_err
    run(scenario())


def test_buffer_dropped_is_result_and_flags_follow_runtime_cursors(tmp_path):
    async def scenario():
        config = ProcessConfig(foreground_timeout=5, stdout_buffer_limit=16, stderr_buffer_limit=16,
                               response_output_limit=8)
        async with dispatched(tmp_path, config=config) as local:
            first = await local.execute("exec_command", {
                "command": "import sys; sys.stdout.write('a'*64); sys.stderr.write('b'*64)",
            })
            assert first["stdout_dropped"] and first["stderr_dropped"]
            assert first["stdout"] == "a" * 8 and first["stderr"] == ""
            pages = [first]
            while pages[-1]["stdout_has_more"] or pages[-1]["stderr_has_more"]:
                pages.append(await local.execute("interact_process", {"process_id": first["process_id"]}))
            assert "".join(p["stdout"] for p in pages) == "a" * 16
            assert "".join(p["stderr"] for p in pages) == "b" * 16
            assert all(not p["stdout_dropped"] and not p["stderr_dropped"] for p in pages[1:])
    run(scenario())


@pytest.mark.parametrize("tool,arguments", [
    ("exec_command", {}), ("exec_command", {"command": 1}),
    ("exec_command", {"command": "pass", "cwd": 1}),
    ("exec_command", {"command": "pass", "workspace": 1}),
    ("exec_command", {"command": "pass", "device": "other"}),
    ("exec_command", {"command": "pass", "timeout": 1}),
    ("exec_command", {"command": "pass", "allowed_roots": []}),
    ("interact_process", {}), ("interact_process", {"process_id": 123}),
    ("interact_process", {"process_id": "id", "input": 1}),
    ("interact_process", {"process_id": "id", "workspace": "."}),
    ("kill_process", {}), ("kill_process", {"process_id": 123}),
    ("kill_process", {"process_id": "id", "force": True}),
    ("kill_process", {"process_id": "id", "device": "other"}),
])
def test_strict_process_arguments(tmp_path, tool, arguments):
    async def scenario():
        async with dispatched(tmp_path) as local:
            with pytest.raises(ToolExecutionError, match="Invalid arguments"):
                await local.execute(tool, arguments)
            assert not local.process_manager.records
    run(scenario())


@pytest.mark.parametrize("tool", ["interact_process", "kill_process"])
@pytest.mark.parametrize("pid", ["missing", "1234"])
def test_unknown_process_and_os_pid_are_tool_errors(tmp_path, tool, pid):
    async def scenario():
        async with dispatched(tmp_path) as local:
            server = create_mcp_server(DeviceRouter("local", local))
            with pytest.raises(ToolError, match="unknown_process"):
                await server.call_tool(tool, {"process_id": pid})
    run(scenario())


def test_exited_stdin_and_already_exited_kill(tmp_path):
    async def scenario():
        async with dispatched(tmp_path, config=ProcessConfig(foreground_timeout=5)) as local:
            result = await local.execute("exec_command", {"command": "pass"})
            pid = result["process_id"]
            with pytest.raises(ToolExecutionError, match="stdin is closed"):
                await local.execute("interact_process", {"process_id": pid, "input": "x"})
            killed = await local.execute("kill_process", {"process_id": pid})
            assert set(killed) == OUTPUT_FIELDS | {"outcome"}
            assert killed["outcome"] == "already_exited" and killed["state"] == "exited"
    run(scenario())


def test_concurrent_kills_keep_final_output_paginated(tmp_path):
    async def scenario():
        config = ProcessConfig(foreground_timeout=0.05, terminate_grace_period=0.05, response_output_limit=8)
        async with dispatched(tmp_path, config=config) as local:
            output = await local.execute("exec_command", {"command": (
                "import sys,time; sys.stdout.write('x'*100); sys.stdout.flush(); time.sleep(60)"
            )})
            pid = output["process_id"]
            record = local.process_manager.records[pid]
            await eventually(lambda: record.stdout.retained_end == 100)
            killed = await asyncio.gather(*(local.execute("kill_process", {"process_id": pid}) for _ in range(3)))
            assert all(p["state"] == "terminated" and p["outcome"] == "terminated" for p in killed)
            assert pid in local.process_manager.records
            pages = [output, *killed]
            while True:
                tail = await local.execute("interact_process", {"process_id": pid})
                pages.append(tail)
                if not tail["stdout_has_more"]:
                    break
            assert "".join(p["stdout"] for p in pages) == "x" * 100
            assert tail["state"] == "terminated" and not tail["draining"]
    run(scenario())


def test_spawn_and_runtime_failures_map_to_tool_errors(tmp_path, monkeypatch):
    async def scenario():
        async with dispatched(tmp_path, shell=ResolvedShell("sh", str(tmp_path / "missing-shell"), ("-c",))) as local:
            with pytest.raises(ToolExecutionError, match="Could not spawn process"):
                await local.execute("exec_command", {"command": "pass"})
            assert not local.process_manager.records
        async with dispatched(tmp_path) as local:
            async def fail(*args, **kwargs):
                raise ProcessError("Runtime failed")
            monkeypatch.setattr(local.process_manager, "spawn", fail)
            with pytest.raises(ToolExecutionError, match="Runtime failed"):
                await local.execute("exec_command", {"command": "pass"})
    run(scenario())


def test_shell_unavailable_is_mcp_tool_error(tmp_path, monkeypatch):
    from chat2local.runtime.shell import ShellError, ShellResolver
    mcp_tools.configure_workspace(tmp_path)
    def fail(self):
        raise ShellError("Configured shell is unavailable")
    monkeypatch.setattr(ShellResolver, "resolve", fail)
    server = create_mcp_server()
    with pytest.raises(ToolError, match="shell is unavailable"):
        run(server.call_tool("exec_command", {"command": "echo x"}))


def test_default_mcp_router_keeps_one_manager_across_process_calls(tmp_path, monkeypatch):
    async def scenario():
        async with dispatched(tmp_path) as local:
            mcp_tools.configure_workspace(tmp_path)
            # Bind a controlled shell while retaining the normal fallback router path.
            from chat2local.dispatch import local as module
            with monkeypatch.context() as scoped:
                scoped.setattr(module, "ProcessManager", lambda config: local.process_manager)
                server = create_mcp_server()
                from test_mcp_surface import payload
                first = payload(await server.call_tool("exec_command", {"command": "import time; time.sleep(60)"}))
                owner = mcp_tools.get_router()
                assert mcp_tools.get_router() is owner
                later = payload(await server.call_tool("interact_process", {"process_id": first["process_id"]}))
                assert later["state"] == "running"
                killed = payload(await server.call_tool("kill_process", {"process_id": first["process_id"]}))
                assert killed["outcome"] == "terminated"
    run(scenario())


def test_incremental_reads_during_concurrent_append_are_lossless(tmp_path):
    async def scenario():
        async with dispatched(tmp_path) as local:
            result = await local.execute("exec_command", {"command": (
                "import sys,time\nfor i in range(40):\n"
                " sys.stdout.write(str(i)+','); sys.stdout.flush()\n"
                " sys.stderr.write(str(i)+':'); sys.stderr.flush(); time.sleep(.004)"
            )})
            pages = [result]
            async with asyncio.timeout(5):
                while pages[-1]["draining"] or pages[-1]["stdout_has_more"] or pages[-1]["stderr_has_more"]:
                    pages.append(await local.execute("interact_process", {"process_id": result["process_id"]}))
                    await asyncio.sleep(0.001)
            assert "".join(p["stdout"] for p in pages) == "".join(f"{i}," for i in range(40))
            assert "".join(p["stderr"] for p in pages) == "".join(f"{i}:" for i in range(40))
    run(scenario())


def test_os_exit_before_pipe_eof_keeps_continuation(tmp_path, monkeypatch):
    async def scenario():
        async with dispatched(tmp_path) as local:
            release = asyncio.Event()
            original = local.process_manager._drain
            async def held(reader, output):
                await release.wait()
                await original(reader, output)
            monkeypatch.setattr(local.process_manager, "_drain", held)
            try:
                result = await local.execute("exec_command", {"command": "print('tail')"})
                record = local.process_manager.records[result["process_id"]]
                await eventually(lambda: record.process.returncode is not None)
                current = await local.execute("interact_process", {"process_id": result["process_id"]})
                assert current["state"] == "exited" and current["draining"]
                assert not current["stdout_has_more"] and current["process_id"]
            finally:
                release.set()
            await local.process_manager.wait(result["process_id"], timeout=5)
            final = await local.execute("interact_process", {"process_id": result["process_id"]})
            assert final["stdout"].strip() == "tail" and not final["draining"]
    run(scenario())


def test_kill_natural_exit_race_maps_already_exited_truthfully(tmp_path, monkeypatch):
    async def scenario():
        async with dispatched(tmp_path) as local:
            result = await local.execute("exec_command", {"command": "import sys; sys.stdin.buffer.read(1)"})
            pid = result["process_id"]
            async def natural_exit(record, *, force):
                await local.process_manager.write_stdin(pid, "x")
                await record.finished.wait()
                return False
            monkeypatch.setattr(local.process_manager, "_signal_tree", natural_exit)
            killed = await local.execute("kill_process", {"process_id": pid})
            assert killed["outcome"] == "already_exited" and killed["state"] == "exited"
            assert killed["exit_code"] == 0
    run(scenario())


@pytest.mark.parametrize("forced", [False, True])
def test_kill_result_mapping_for_graceful_and_forced_paths(tmp_path, monkeypatch, forced):
    async def scenario():
        async with dispatched(tmp_path) as local:
            result = await local.execute("exec_command", {"command": "import sys; sys.stdin.buffer.read(1)"})
            pid = result["process_id"]
            calls = []
            async def signal(record, *, force):
                calls.append(force)
                if forced and not force:
                    return False
                record.termination_sent = True
                await local.process_manager.write_stdin(pid, "x")
                await record.finished.wait()
                return True
            # Deterministic manager signaling boundary; Tool still exercises real
            # runtime grace/escalation and output mapping, not a fake Tool result.
            monkeypatch.setattr(local.process_manager, "_signal_tree", signal)
            killed = await local.execute("kill_process", {"process_id": pid})
            assert calls == ([False, True] if forced else [False])
            assert killed["outcome"] == killed["state"] == "terminated"
            assert killed["exit_code"] == 0 and not killed["draining"]
    run(scenario())


def test_command_with_nul_is_tool_error_and_not_spawned(tmp_path):
    async def scenario():
        async with dispatched(tmp_path) as local:
            with pytest.raises(ToolExecutionError, match="NUL"):
                await local.execute("exec_command", {"command": "echo\x00x"})
            assert not local.process_manager.records
    run(scenario())
