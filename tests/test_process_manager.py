"""Real subprocess lifecycle plus deterministic buffer and race tests."""

import asyncio
from contextlib import asynccontextmanager
import ctypes
import errno
import os
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from uuid import UUID

import pytest

from chat2local.runtime import process_manager as module
from chat2local.runtime.config import ProcessConfig
from chat2local.runtime.process_manager import (
    MAX_FINISHED_PROCESSES, OutputBuffer, ProcessError, ProcessManager,
    StdinClosedError, UnknownProcessError,
)
from chat2local.runtime.shell import ResolvedShell, ShellResolver
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager
from conftest import run
from test_apply_patch import create_link


# A controlled executable exercises the manager without shell quoting in tests.
# Actual platform shell execution is separately tested below.
PYTHON = ResolvedShell("sh", sys.executable, ("-u", "-X", "utf8", "-c"))
NEWLINE = os.linesep


@asynccontextmanager
async def managed(config=None, shell=PYTHON):
    config = config or ProcessConfig(terminate_grace_period=0.05, foreground_timeout=0.05)
    manager = ProcessManager(config, shell=shell)
    try:
        yield manager
    finally:
        await manager.shutdown()


async def eventually(predicate, timeout=5):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


async def finish(manager, record):
    status = await manager.wait(record.process_id, timeout=10)
    assert not status.draining and status.state != "running"
    return status


def test_short_nonzero_process_and_opaque_identity(workspace):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn(
                "import sys; print('out'); print('err',file=sys.stderr); sys.exit(7)", workspace=workspace,
            )
            assert UUID(record.process_id).version == 4
            assert record.process_id != str(record.process.pid)
            assert manager.records[record.process_id] is record
            status = await finish(manager, record)
            assert status.state == "exited" and status.exit_code == 7
            output = manager.read_output(record.process_id)
            assert output.stdout.text == "out" + NEWLINE and output.stderr.text == "err" + NEWLINE
            assert output.stdout.read_cursor == output.stderr.read_cursor == 3 + len(NEWLINE)
            again = manager.read_output(record.process_id)
            assert again.stdout.text == again.stderr.text == ""
            assert not again.stdout.has_more and not again.stderr.has_more
            assert (await manager.terminate(record.process_id)).outcome == "already_exited"
            assert manager.status(record.process_id).state == "exited"
    run(scenario())


def test_successful_exit(workspace):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("print('success')", workspace=workspace)
            status = await finish(manager, record)
            assert status.exit_code == 0 and status.state == "exited"
            assert manager.read_output(record.process_id).stdout.text == "success" + NEWLINE
    run(scenario())


@pytest.mark.parametrize("unknown", ["missing", "123", "00000000-0000-0000-0000-000000000000"])
def test_unknown_ids_never_fall_back_to_os_pid(workspace, unknown):
    async def scenario():
        async with managed() as manager:
            for call in (manager.status, manager.read_output):
                with pytest.raises(UnknownProcessError):
                    call(unknown)
            with pytest.raises(UnknownProcessError):
                await manager.wait(unknown)
            with pytest.raises(UnknownProcessError):
                await manager.write_stdin(unknown, "x")
            with pytest.raises(UnknownProcessError):
                await manager.terminate(unknown)
    run(scenario())


def test_workspace_cwd_uses_current_workspace_policy(workspace, tmp_path):
    (workspace.root / "sub").mkdir()
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("import os; print(os.getcwd())", workspace=workspace, cwd="sub")
            await finish(manager, record)
            assert manager.read_output(record.process_id).stdout.text.strip() == str(workspace.root / "sub")
            for cwd in (tmp_path, "../outside"):
                with pytest.raises(WorkspaceError):
                    await manager.spawn("print('must not run')", workspace=workspace, cwd=cwd)
            with pytest.raises(ProcessError, match="directory"):
                await manager.spawn("print('must not run')", workspace=workspace, cwd="missing")
    run(scenario())


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_cwd_links_cannot_escape(workspace, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    create_link(workspace.root / "link", outside, kind)
    async def scenario():
        async with managed() as manager:
            with pytest.raises(WorkspaceError):
                await manager.spawn("print('must not run')", workspace=workspace, cwd="link")
            assert not manager.records
    run(scenario())


def test_multiple_devices_and_concurrent_processes_are_independent(workspace):
    async def scenario():
        async with managed() as one, managed() as two:
            records = await asyncio.gather(*(
                one.spawn(f"import time; time.sleep(0.03); print({i})", workspace=workspace) for i in range(6)
            ))
            await asyncio.gather(*(finish(one, record) for record in records))
            outputs = {one.read_output(record.process_id).stdout.text for record in records}
            assert outputs == {str(i) + NEWLINE for i in range(6)}
            with pytest.raises(UnknownProcessError):
                two.status(records[0].process_id)
            assert two.records == {} and len(one.records) == 6
    run(scenario())


def test_output_is_drained_while_running_and_wait_timeout_does_not_kill(workspace):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("import time; print('ready',flush=True); time.sleep(30)", workspace=workspace)
            await eventually(lambda: record.stdout.retained_end > 0)
            status = await manager.wait(record.process_id)
            assert status.state == "running" and status.exit_code is None
            assert manager.read_output(record.process_id).stdout.text == "ready" + NEWLINE
            assert record.process.returncode is None
            with pytest.raises(ValueError):
                await manager.wait(record.process_id, timeout=0)
    run(scenario())


def test_large_stdout_stderr_do_not_deadlock_and_retain_byte_budget(workspace):
    config = ProcessConfig(stdout_buffer_limit=1001, stderr_buffer_limit=701, terminate_grace_period=0.05)
    async def scenario():
        async with managed(config) as manager:
            record = await manager.spawn(
                "import os; os.write(1,b'a'*2000000+b'END'); os.write(2,b'b'*2000000+b'ERR')", workspace=workspace)
            await finish(manager, record)
            assert record.stdout.retained_bytes == 1001 and record.stderr.retained_bytes == 701
            assert record.stdout.retained_end == record.stderr.retained_end == 2000003
            assert record.stdout.retained_start == 2000003 - 1001
            assert record.stderr.retained_start == 2000003 - 701
            result = manager.read_output(record.process_id)
            assert result.stdout.text == "a" * 998 + "END" and result.stderr.text == "b" * 698 + "ERR"
            assert result.stdout.output_dropped and result.stderr.output_dropped
            assert not manager.read_output(record.process_id).stdout.output_dropped
    run(scenario())


def test_response_limit_retains_unread_finished_output(workspace):
    async def scenario():
        async with managed(ProcessConfig(response_output_limit=10, terminate_grace_period=0.05)) as manager:
            record = await manager.spawn("import os; os.write(1,b'a'*33); os.write(2,b'b'*19)", workspace=workspace)
            await finish(manager, record)
            stdout, stderr = "", ""
            chunks = []
            while True:
                result = manager.read_output(record.process_id)
                chunks.append(result)
                assert len(result.stdout.text.encode()) + len(result.stderr.text.encode()) <= 10
                stdout += result.stdout.text
                stderr += result.stderr.text
                if not result.stdout.has_more and not result.stderr.has_more:
                    break
            assert stdout == "a" * 33 and stderr == "b" * 19 and len(chunks) == 6
            assert record.stdout.retained_bytes == 33 and record.stderr.retained_bytes == 19
            assert chunks[0].stdout.read_cursor == 10 and chunks[0].stderr.read_cursor == 0
            with pytest.raises(ValueError):
                manager.read_output(record.process_id, limit=1)
    run(scenario())


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 7, 10])
def test_utf8_buffers_drop_whole_oldest_characters(limit):
    buffer = OutputBuffer(limit)
    buffer.append("你好🙂a")
    assert buffer.retained_bytes <= limit
    assert buffer.retained_start + buffer.retained_bytes == buffer.retained_end == 11
    result = buffer.read(100)
    assert "你好🙂a".endswith(result.text) and result.output_dropped
    assert result.read_cursor == 11
    buffer.append("next")
    assert buffer.retained_end == 15 and buffer.retained_start >= result.retained_start
    assert buffer.retained_bytes <= limit


def test_cursor_eviction_and_offsets_never_reset():
    buffer = OutputBuffer(5)
    buffer.append("abc")
    assert buffer.read(2).text == "ab"
    buffer.append("defgh")
    read = buffer.read(2)
    assert read.text == "de" and read.output_dropped
    assert (read.retained_start, read.retained_end, read.read_cursor) == (3, 8, 5)
    assert read.has_more
    read = buffer.read(100)
    assert read.text == "fgh" and not read.output_dropped and read.read_cursor == 8


def test_response_prefix_never_splits_unicode_and_preserves_next_character():
    buffer = OutputBuffer(100)
    buffer.append("你🙂a")
    assert buffer.read(4).text == "你"
    assert buffer.read(4).text == "🙂"
    assert buffer.read(4).text == "a"
    assert buffer.read(4).text == ""


def test_decoder_multibyte_splits_invalid_bytes_and_eof_flush(workspace, monkeypatch):
    monkeypatch.setattr(module, "PIPE_READ_SIZE", 1)
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn(
                "import os; os.write(1,'你好🙂'.encode()+b'\\xff\\xe4'); os.write(2,'stderr🙂'.encode())",
                workspace=workspace,
            )
            await finish(manager, record)
            output = manager.read_output(record.process_id)
            assert output.stdout.text == "你好🙂��" and output.stderr.text == "stderr🙂"
            assert output.stdout.retained_end == len("你好🙂��".encode())
    run(scenario())


def test_final_output_after_os_exit_is_drained_before_finished(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            original = manager._drain
            gate = asyncio.Event()
            async def delayed(reader, output):
                await gate.wait()
                await original(reader, output)
            monkeypatch.setattr(manager, "_drain", delayed)
            record = await manager.spawn("print('last output')", workspace=workspace)
            await eventually(lambda: record.process.returncode is not None)
            assert record.state == "exited" and not record.finished.is_set() and record.finished_at is None
            gate.set()
            await finish(manager, record)
            assert manager.read_output(record.process_id).stdout.text == "last output" + NEWLINE
    run(scenario())


@pytest.mark.parametrize("text", ["abc", "yes\n", "你好🙂"])
def test_exact_stdin_input_no_added_newline(workspace, text):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn(
                "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)", workspace=workspace,
            )
            await manager.write_stdin(record.process_id, text)
            await manager.close_stdin(record.process_id)
            await finish(manager, record)
            assert manager.read_output(record.process_id).stdout.text == text
            with pytest.raises(StdinClosedError):
                await manager.write_stdin(record.process_id, "after exit")
    run(scenario())


def test_stdin_write_and_process_exit_race_is_handled(workspace):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("import time; time.sleep(0.03)", workspace=workspace)
            try:
                await asyncio.wait_for(manager.write_stdin(record.process_id, "x" * 2000000), 5)
            except StdinClosedError:
                pass
            await finish(manager, record)
            assert record.state == "exited"
    run(scenario())


def test_terminate_and_force_requests_share_one_lifecycle(workspace):
    async def scenario():
        async with managed(ProcessConfig(terminate_grace_period=5)) as manager:
            record = await manager.spawn("import time; print('before kill'); time.sleep(30)", workspace=workspace)
            await eventually(lambda: record.stdout.retained_end > 0)
            normal = asyncio.create_task(manager.terminate(record.process_id))
            await asyncio.sleep(0.01)
            forced = asyncio.create_task(manager.terminate(record.process_id, force=True))
            results = await asyncio.wait_for(asyncio.gather(normal, forced), 5)
            assert all(result.status.state == "terminated" for result in results)
            assert record.finished.is_set() and record.finished_at is not None
            assert manager.read_output(record.process_id).stdout.text == "before kill" + NEWLINE
            assert all(task.done() for task in record.drain_tasks)
    run(scenario())


def test_terminate_already_exited_never_sends_signal(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("pass", workspace=workspace)
            await finish(manager, record)
            async def forbidden(*args, **kwargs):
                raise AssertionError("Cannot signal an exited process")
            monkeypatch.setattr(manager, "_signal_tree", forbidden)
            result = await manager.terminate(record.process_id, force=True)
            assert result.outcome == "already_exited" and result.status.state == "exited"
    run(scenario())


def test_natural_exit_vs_terminate_race_preserves_valid_state(workspace):
    async def scenario():
        async with managed() as manager:
            for _ in range(5):
                record = await manager.spawn("pass", workspace=workspace)
                await asyncio.sleep(0.01)
                result = await manager.terminate(record.process_id)
                assert result.status.state in ("exited", "terminated")
                await finish(manager, record)
                if not record.termination_sent:
                    assert record.state == "exited"
    run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
@pytest.mark.parametrize("ignore", [False, True])
def test_posix_grace_and_forced_group_signals(workspace, ignore, monkeypatch):
    async def scenario():
        async with managed(ProcessConfig(terminate_grace_period=0.08)) as manager:
            signals = []
            original = module.os.killpg
            def killpg(pid, value):
                signals.append(value)
                return original(pid, value)
            monkeypatch.setattr(module.os, "killpg", killpg)
            setup = "import signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); " if ignore else ""
            record = await manager.spawn(setup + "import time; print('ready'); time.sleep(30)", workspace=workspace)
            await eventually(lambda: record.stdout.retained_end > 0)
            start = asyncio.get_running_loop().time()
            result = await manager.terminate(record.process_id)
            elapsed = asyncio.get_running_loop().time() - start
            assert signals == ([signal.SIGTERM, signal.SIGKILL] if ignore else [signal.SIGTERM])
            assert result.status.exit_code == (-signal.SIGKILL if ignore else -signal.SIGTERM)
            if ignore:
                assert elapsed >= 0.07
    run(scenario())


def pid_alive(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # On Linux a killed child may temporarily be a zombie awaiting reaping.
    status = Path(f"/proc/{pid}/stat")
    if sys.platform.startswith("linux") and status.exists():
        return status.read_text().split(") ", 1)[1][0] != "Z"
    return True


@pytest.mark.parametrize("shutdown", [False, True])
def test_real_process_tree_is_terminated_and_output_drained(workspace, shutdown):
    async def scenario():
        async with managed() as manager:
            code = (
                "import subprocess,sys,time; "
                "child=subprocess.Popen([sys.executable,'-u','-c',"
                "\"import time; print('child output'); time.sleep(30)\"]); "
                "print('CHILD='+str(child.pid),flush=True); time.sleep(30)"
            )
            record = await manager.spawn(code, workspace=workspace)
            await eventually(lambda: record.stdout.retained_end > 0)
            text = ""
            while "CHILD=" not in text or "\n" not in text.split("CHILD=", 1)[-1]:
                text += manager.read_output(record.process_id).stdout.text
                await asyncio.sleep(0.005)
            child_pid = int(text.split("CHILD=", 1)[1].splitlines()[0])
            await eventually(lambda: pid_alive(child_pid))
            if shutdown:
                await manager.shutdown()
            else:
                result = await manager.terminate(record.process_id)
                assert result.status.state == "terminated"
            await eventually(lambda: not pid_alive(child_pid))
            assert record.process.returncode is not None and record.finished.is_set()
            assert all(task.done() for task in record.drain_tasks)
    run(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill tree")
def test_windows_taskkill_uses_tree_and_force_flags(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            original = module.asyncio.create_subprocess_exec
            arguments = []
            async def create(*args, **kwargs):
                if Path(args[0]).name.lower() == "taskkill.exe":
                    arguments.append(args)
                return await original(*args, **kwargs)
            monkeypatch.setattr(module.asyncio, "create_subprocess_exec", create)
            record = await manager.spawn("import time; print('ready'); time.sleep(30)", workspace=workspace)
            await eventually(lambda: record.stdout.retained_end > 0)
            await manager.terminate(record.process_id)
            assert arguments[0][1:] == ("/PID", str(record.process.pid), "/T")
            assert arguments[-1][1:] == ("/PID", str(record.process.pid), "/T", "/F")
    run(scenario())


def test_finished_retention_starts_after_drain_and_preserves_valid_output(workspace, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("print('retained')", workspace=workspace)
            await finish(manager, record)
            clock[0] = 1600
            assert manager.read_output(record.process_id).stdout.text == "retained" + NEWLINE
            clock[0] = 1600.01
            manager.cleanup()
            with pytest.raises(UnknownProcessError):
                manager.status(record.process_id)
    run(scenario())


def test_finished_count_bound_never_evicts_running_records():
    manager = ProcessManager(shell=PYTHON)
    now = module.monotonic()
    for index in range(70):
        manager._records[f"running-{index}"] = SimpleNamespace(process_id=f"running-{index}", finished_at=None)
        manager._records[f"done-{index}"] = SimpleNamespace(process_id=f"done-{index}", finished_at=now + index)
    manager.cleanup()
    assert len(manager.records) == 70 + MAX_FINISHED_PROCESSES
    assert all(f"running-{index}" in manager.records for index in range(70))
    assert all(f"done-{index}" not in manager.records for index in range(6))
    assert "done-6" in manager.records and "done-69" in manager.records


def test_cleanup_can_occur_while_wait_caller_holds_record(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("import time; time.sleep(0.02)", workspace=workspace)
            waiter = asyncio.create_task(manager.wait(record.process_id, timeout=5))
            await asyncio.sleep(0)
            await record.finished.wait()
            monkeypatch.setattr(module, "monotonic", lambda: record.finished_at + 601)
            manager.cleanup()
            assert record.process_id not in manager.records
            assert (await waiter).state == "exited"
    run(scenario())


def test_shutdown_idempotence_rejects_spawn_and_leaves_no_internal_tasks(workspace):
    async def scenario():
        manager = ProcessManager(ProcessConfig(terminate_grace_period=0.05), shell=PYTHON)
        records = await asyncio.gather(*(
            manager.spawn("import time; print('started'); time.sleep(30)", workspace=workspace) for _ in range(3)
        ))
        await asyncio.gather(manager.shutdown(), manager.shutdown())
        await manager.shutdown()
        assert manager.records == {}
        assert all(record.state == "terminated" and record.finished.is_set() for record in records)
        assert all(task.done() for record in records for task in record.drain_tasks)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()
                    and task.get_name().startswith("chat2local-") and not task.done()]
        with pytest.raises(ProcessError, match="shut down"):
            await manager.spawn("pass", workspace=workspace)
    run(scenario())


def test_cancelled_spawn_cannot_abandon_process_and_shutdown_waits_for_spawn(workspace, monkeypatch):
    async def scenario():
        manager = ProcessManager(ProcessConfig(terminate_grace_period=0.05), shell=PYTHON)
        original = module.asyncio.create_subprocess_exec
        spawned, release = asyncio.Event(), asyncio.Event()
        processes = []
        async def delayed(*args, **kwargs):
            process = await original(*args, **kwargs)
            if args[0] == sys.executable:
                processes.append(process)
                spawned.set()
                await release.wait()
            return process
        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", delayed)
        caller = asyncio.create_task(manager.spawn("import time; time.sleep(30)", workspace=workspace))
        await spawned.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        shutdown = asyncio.create_task(manager.shutdown())
        await asyncio.sleep(0.01)
        assert not shutdown.done()
        release.set()
        await asyncio.wait_for(shutdown, 5)
        assert processes[0].returncode is not None and manager.records == {} and not manager._spawning
    run(scenario())


def test_wait_cancellation_does_not_cancel_lifecycle(workspace):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("import time; time.sleep(0.1); print('done')", workspace=workspace)
            waiter = asyncio.create_task(manager.wait(record.process_id, timeout=10))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not record.watch_task.done()
            await finish(manager, record)
            assert manager.read_output(record.process_id).stdout.text == "done" + NEWLINE
    run(scenario())


def test_spawn_os_error_is_readable_and_no_host_path_or_record(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            async def fail(*args, **kwargs):
                raise FileNotFoundError(errno.ENOENT, "Missing executable", "PRIVATE/HOST/PATH")
            monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fail)
            with pytest.raises(ProcessError, match="Missing executable") as failure:
                await manager.spawn("pass", workspace=workspace)
            assert "PRIVATE" not in str(failure.value) and not manager.records
    run(scenario())


def test_programmer_error_not_wrapped_as_runtime_error(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            async def fail(*args, **kwargs):
                raise KeyError("bug")
            monkeypatch.setattr(module.asyncio, "create_subprocess_exec", fail)
            with pytest.raises(KeyError):
                await manager.spawn("pass", workspace=workspace)
    run(scenario())


def test_actual_platform_shell_runs_utf8_command(workspace):
    selected = ShellResolver().resolve()
    command = "[Console]::Write('你好🙂')" if selected.kind in ("pwsh", "powershell") else (
        "echo hello" if selected.kind == "cmd" else "printf '你好🙂'")
    async def scenario():
        async with managed(shell=selected) as manager:
            record = await manager.spawn(command, workspace=workspace)
            assert (await finish(manager, record)).exit_code == 0
            result = manager.read_output(record.process_id)
            assert result.stdout.text.strip() == ("hello" if selected.kind == "cmd" else "你好🙂")
    run(scenario())


def test_default_response_budget_pages_100_kib_after_exit(workspace):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("import os; os.write(1,b'x'*102400)", workspace=workspace)
            assert (await manager.wait(record.process_id, timeout=5)).state == "exited"
            chunks = [manager.read_output(record.process_id) for _ in range(4)]
            assert [len(chunk.stdout.text.encode()) for chunk in chunks] == [32768, 32768, 32768, 4096]
            assert all(chunk.stdout.has_more for chunk in chunks[:3]) and not chunks[-1].stdout.has_more
            assert record.stdout.retained_bytes == 102400
    run(scenario())


def test_real_child_environment_inherits_user_values(workspace, monkeypatch):
    monkeypatch.setenv("CHAT2LOCAL_TEST_CUSTOM", "preserved")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy-test")
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn(
                "import os,json; print(json.dumps({k:os.environ.get(k) for k in "
                "['CHAT2LOCAL_TEST_CUSTOM','HTTP_PROXY','NO_COLOR','COLORTERM','GIT_PAGER','GH_PAGER']}))",
                workspace=workspace,
            )
            await finish(manager, record)
            import json
            assert json.loads(manager.read_output(record.process_id).stdout.text) == {
                "CHAT2LOCAL_TEST_CUSTOM": "preserved", "HTTP_PROXY": "http://proxy-test",
                "NO_COLOR": "1", "COLORTERM": "", "GIT_PAGER": "cat", "GH_PAGER": "cat",
            }
    run(scenario())


def test_short_wait_uses_configured_foreground_timeout(workspace):
    async def scenario():
        async with managed(ProcessConfig(foreground_timeout=2, terminate_grace_period=0.05)) as manager:
            record = await manager.spawn("pass", workspace=workspace)
            assert (await manager.wait(record.process_id)).state == "exited"
    run(scenario())


def test_concurrent_read_append_and_eviction_are_atomic(workspace):
    async def scenario():
        async with managed(ProcessConfig(stdout_buffer_limit=16, response_output_limit=4,
                                         terminate_grace_period=0.05)) as manager:
            record = await manager.spawn(
                "import os,time; "
                "[(os.write(1,str(i).zfill(4).encode()),time.sleep(0.004)) for i in range(40)]", workspace=workspace)
            previous_start, previous_end, previous_cursor = 0, 0, 0
            text = ""
            dropped = False
            while not record.finished.is_set() or record.stdout.read_cursor < record.stdout.retained_end:
                output = manager.read_output(record.process_id).stdout
                assert output.retained_start >= previous_start and output.retained_end >= previous_end
                assert output.read_cursor >= previous_cursor and record.stdout.retained_bytes <= 16
                previous_start = output.retained_start
                previous_end = output.retained_end
                previous_cursor = output.read_cursor
                dropped |= output.output_dropped
                text += output.text
                await asyncio.sleep(0.025)
            assert dropped and record.stdout.retained_end == 160 and text.endswith("0039")
            assert record.stdout.read_cursor == 160
    run(scenario())


def test_natural_exit_during_unsuccessful_termination_remains_exited(workspace, monkeypatch):
    async def scenario():
        async with managed(ProcessConfig(terminate_grace_period=1)) as manager:
            record = await manager.spawn("import time; time.sleep(0.05)", workspace=workspace)
            async def unsuccessful(record, *, force):
                return False
            monkeypatch.setattr(manager, "_signal_tree", unsuccessful)
            result = await manager.terminate(record.process_id)
            assert result.outcome == "already_exited" and result.status.state == "exited"
            assert not record.termination_sent and record.process.returncode == 0
    run(scenario())


def test_cancelled_terminate_and_shutdown_leave_runtime_tasks_alive_until_reaped(workspace):
    async def scenario():
        manager = ProcessManager(ProcessConfig(terminate_grace_period=0.2), shell=PYTHON)
        try:
            record = await manager.spawn("import time; print('ready'); time.sleep(30)", workspace=workspace)
            await eventually(lambda: record.stdout.retained_end > 0)
            caller = asyncio.create_task(manager.terminate(record.process_id))
            await asyncio.sleep(0)
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            assert record.termination_task is not None and not record.termination_task.cancelled()
            shutdown = asyncio.create_task(manager.shutdown())
            await asyncio.sleep(0)
            shutdown.cancel()
            with pytest.raises(asyncio.CancelledError):
                await shutdown
            await manager.shutdown()
            assert record.finished.is_set() and not manager.records
        finally:
            await manager.shutdown()
    run(scenario())


@pytest.mark.parametrize("failure", ["os_error", "programmer_error"])
def test_drain_failure_is_reported_and_does_not_deadlock_producer(workspace, monkeypatch, failure):
    async def scenario():
        manager = ProcessManager(ProcessConfig(terminate_grace_period=0.05), shell=PYTHON)
        original = manager._drain
        count = [0]
        async def broken(reader, output):
            count[0] += 1
            if count[0] == 1:
                if failure == "os_error":
                    raise OSError(errno.EIO, "Read failed", "PRIVATE-HOST-PATH")
                raise KeyError("reader bug")
            return await original(reader, output)
        monkeypatch.setattr(manager, "_drain", broken)
        record = await manager.spawn("import os,time; os.write(1,b'x'*1000000); time.sleep(30)", workspace=workspace)
        expected = ProcessError if failure == "os_error" else KeyError
        try:
            await asyncio.wait_for(record.finished.wait(), 5)
            with pytest.raises(expected) as caught:
                manager.read_output(record.process_id)
            assert "PRIVATE" not in str(caught.value)
            with pytest.raises(expected):
                await manager.wait(record.process_id)
            assert record.process.returncode is not None
            assert all(task.done() for task in record.drain_tasks)
        finally:
            with pytest.raises(expected):
                await manager.shutdown()
            assert manager.records == {}
            await manager.shutdown()
    run(scenario())


def test_termination_os_failure_is_not_swallowed_and_can_be_retried(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            record = await manager.spawn("import time; print('ready'); time.sleep(30)", workspace=workspace)
            await eventually(lambda: record.stdout.retained_end > 0)
            with monkeypatch.context() as patch:
                async def fail(*args, **kwargs):
                    raise ProcessError("OS refused tree termination")
                patch.setattr(manager, "_signal_tree", fail)
                with pytest.raises(ProcessError, match="OS refused"):
                    await manager.terminate(record.process_id)
                assert record.state == "running" and record.process_id in manager.records
            assert (await manager.terminate(record.process_id, force=True)).status.state == "terminated"
    run(scenario())


def test_shutdown_failure_is_reported_then_retry_closes_remaining_process(workspace, monkeypatch):
    async def scenario():
        manager = ProcessManager(ProcessConfig(terminate_grace_period=0.05), shell=PYTHON)
        record = await manager.spawn("import time; time.sleep(30)", workspace=workspace)
        with monkeypatch.context() as patch:
            async def fail(*args, **kwargs):
                raise ProcessError("OS refused tree termination")
            patch.setattr(manager, "_signal_tree", fail)
            with pytest.raises(ProcessError, match="OS refused"):
                await manager.shutdown()
            assert record.process_id in manager.records
            with pytest.raises(ProcessError, match="shut down"):
                await manager.spawn("pass", workspace=workspace)
        await manager.shutdown()
        assert record.process.returncode is not None and manager.records == {}
    run(scenario())


@pytest.mark.skipif(os.name == "nt", reason="POSIX group ownership")
def test_posix_ignored_child_is_forced_even_after_parent_exits(workspace):
    async def scenario():
        async with managed(ProcessConfig(terminate_grace_period=0.1)) as manager:
            record = await manager.spawn(
                "import subprocess,sys,time; child=subprocess.Popen([sys.executable,'-u','-c',"
                "\"import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                "print('child ready'); time.sleep(30)\"]);"
                "print('CHILD='+str(child.pid)); time.sleep(30)", workspace=workspace,
            )
            text = ""
            while "child ready" not in text:
                text += manager.read_output(record.process_id).stdout.text
                await asyncio.sleep(0.005)
            child_pid = int(text.split("CHILD=", 1)[1].splitlines()[0])
            assert (await manager.terminate(record.process_id)).status.state == "terminated"
            await eventually(lambda: not pid_alive(child_pid))
    run(scenario())


@pytest.mark.skipif(os.name != "nt", reason="Windows termination classification")
def test_finished_waits_for_taskkill_result_before_classifying_exit(workspace, monkeypatch):
    async def scenario():
        async with managed() as manager:
            original = module.asyncio.create_subprocess_exec
            killed, release = asyncio.Event(), asyncio.Event()
            class DelayedHelper:
                def __init__(self, process):
                    self.process = process
                @property
                def returncode(self):
                    return self.process.returncode
                async def wait(self):
                    result = await self.process.wait()
                    killed.set()
                    await release.wait()
                    return result
                def kill(self):
                    self.process.kill()
            async def create(*args, **kwargs):
                process = await original(*args, **kwargs)
                return DelayedHelper(process) if Path(args[0]).name.lower() == "taskkill.exe" else process
            monkeypatch.setattr(module.asyncio, "create_subprocess_exec", create)
            record = await manager.spawn("import time; time.sleep(30)", workspace=workspace)
            caller = asyncio.create_task(manager.terminate(record.process_id, force=True))
            try:
                await asyncio.wait_for(killed.wait(), 5)
                await eventually(lambda: record.process.returncode is not None)
                assert not record.finished.is_set() and record.finished_at is None
                assert (await manager.wait(record.process_id, timeout=0.01)).draining
            finally:
                release.set()
            result = await caller
            assert result.status.state == "terminated" and not result.status.draining
            assert (await manager.wait(record.process_id)).state == "terminated"
    run(scenario())
