"""Inherited pipe closure can stop writers; independent redirection avoids it."""

import asyncio
import json
import os

import pytest

from chat2local.runtime import process_manager as module
from chat2local.runtime.process_manager import ProcessManager, ProcessOutput
from chat2local.tools.kill_process import kill_process
from chat2local.tools.process_output import serialize_output
from conftest import HIDDEN_SUBPROCESS_OPTIONS, run
from test_process_manager import PYTHON, eventually, pid_alive


# Observe an unhandled exception through an independent file, without catching
# it, recovering, redirecting streams, or allowing the task to continue.
WRITER = """
import json, sys, time
from pathlib import Path
root, stream = Path(sys.argv[1]), sys.argv[2]
phase = 'starting'
def report_uncaught(kind, error, traceback):
    (root / 'uncaught.json').write_text(json.dumps({'type': kind.__name__, 'phase': phase}))
    sys.__excepthook__(kind, error, traceback)
sys.excepthook = report_uncaught
print('child-out', flush=True)
print('child-err', file=sys.stderr, flush=True)
(root / 'ready').touch()
deadline = time.monotonic() + 15
count = 0
while not (root / 'finish-work').exists():
    if time.monotonic() > deadline:
        raise TimeoutError('test did not release writer')
    phase = 'writing'
    print(f'{stream}:{count}', file=getattr(sys, stream), flush=True)
    count += 1
    time.sleep(0.005)
phase = 'finishing'
print('child-out-end', flush=True)
print('child-err-end', file=sys.stderr, flush=True)
(root / 'completed').write_text(str(count))
"""


async def spawn_writer(manager, workspace, destination, stream):
    root = workspace.root
    script = root / "writer.py"
    script.write_text(WRITER, encoding="utf-8")
    if destination == "inherited":
        targets = "out = err = None"
    else:
        stdout = str(root / "child.stdout") if destination == "files" else os.devnull
        stderr = str(root / "child.stderr") if destination == "files" else os.devnull
        targets = f"out = open({stdout!r}, 'w'); err = open({stderr!r}, 'w')"
    # A exits immediately after launching B; the test observes B independently.
    command = (
        "import subprocess,sys; from pathlib import Path; " + targets + "; "
        f"child = subprocess.Popen([sys.executable, '-u', {str(script)!r}, {str(root)!r}, {stream!r}], "
        f"stdin=subprocess.DEVNULL, stdout=out, stderr=err, **{HIDDEN_SUBPROCESS_OPTIONS!r}); "
        f"Path({str(root / 'child.pid')!r}).write_text(str(child.pid)); "
        "print('parent-out', flush=True); print('parent-err', file=sys.stderr, flush=True)"
    )
    record = await manager.spawn(command, workspace=workspace)
    await eventually(lambda: record.process.returncode is not None and (root / "ready").exists())
    return record, int((root / "child.pid").read_text())


@pytest.mark.parametrize("operation", ["terminate", "shutdown"])
@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("destination", ["inherited", "files", "devnull"])
def test_post_exit_cleanup_effect_on_descendant_writes(workspace, monkeypatch, operation, stream, destination):
    monkeypatch.setattr(module, "OUTPUT_DRAIN_TIMEOUT", 0.08)
    root = workspace.root

    async def scenario():
        manager = ProcessManager(shell=PYTHON)
        child_pid = None
        try:
            record, child_pid = await spawn_writer(manager, workspace, destination, stream)
            assert record.process.returncode == 0 and pid_alive(child_pid)
            if destination == "inherited":
                # Prove the unhandled writer is active before closing the pipes.
                buffer = getattr(record, stream)
                label = "out" if stream == "stdout" else "err"
                initial = len(f"parent-{label}{os.linesep}child-{label}{os.linesep}".encode())
                await eventually(lambda: buffer.retained_end > initial)
                assert record.status().draining and not record.status().output_incomplete

            async def forbidden(*args, **kwargs):
                raise AssertionError("Cleanup must not signal the exited parent's old PID")
            monkeypatch.setattr(manager, "_signal_tree", forbidden)
            monkeypatch.setattr(manager, "_windows_signal_tree", forbidden)
            if operation == "terminate":
                # Also exercise the actual Tool response and incomplete flag.
                output = await asyncio.wait_for(kill_process(manager, record.process_id), 3)
                assert output["outcome"] == "already_exited"
            else:
                await asyncio.wait_for(manager.shutdown(), 3)
                # Shutdown clears the registry; inspect the retained test record.
                output = serialize_output(ProcessOutput(
                    record.status(), record.stdout.read(32768), record.stderr.read(32768),
                ))
            assert output["state"] == "exited" and output["exit_code"] == 0
            assert not output["draining"] and not record.termination_sent
            assert record.watch_task.done() and all(task.done() for task in record.drain_tasks)
            assert not output["stdout_dropped"] and not output["stderr_dropped"]

            if destination == "inherited":
                assert output["output_incomplete"] is True
                assert "parent-out" in output["stdout"] and "child-out" in output["stdout"]
                assert "parent-err" in output["stderr"] and "child-err" in output["stderr"]
                await eventually(lambda: not pid_alive(child_pid))
                assert not (root / "completed").exists(), "Broken writes must not be hidden by fixture recovery"
                # Exception classes, errno values, exit codes and SIGPIPE behavior
                # vary. An excepthook record is optional if the OS ended B first.
                report = root / "uncaught.json"
                if report.exists():
                    error = json.loads(report.read_text())
                    assert error["phase"] == "writing" and error["type"]
            else:
                assert "output_incomplete" not in output and not record.output_incomplete
                assert output["stdout"] == "parent-out" + os.linesep
                assert output["stderr"] == "parent-err" + os.linesep
                assert pid_alive(child_pid) and not (root / "completed").exists()
                (root / "finish-work").touch()
                await eventually((root / "completed").exists)
                await eventually(lambda: not pid_alive(child_pid))
                assert not (root / "uncaught.json").exists()
                if destination == "files":
                    count = int((root / "completed").read_text())
                    for name, label in (("stdout", "out"), ("stderr", "err")):
                        lines = [f"child-{label}"]
                        if stream == name:
                            lines.extend(f"{stream}:{i}" for i in range(count))
                        lines.append(f"child-{label}-end")
                        assert (root / f"child.{name}").read_text().splitlines() == lines
            await manager.shutdown()
        finally:
            # Release only this temporary writer, including assertion failures.
            (root / "finish-work").touch()
            await asyncio.wait_for(manager.shutdown(), 3)
            if child_pid is None and (root / "child.pid").exists():
                child_pid = int((root / "child.pid").read_text())
            if child_pid is not None:
                await eventually(lambda: not pid_alive(child_pid))

    run(scenario())
