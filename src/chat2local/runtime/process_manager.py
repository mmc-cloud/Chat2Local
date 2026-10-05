"""Device-local PIPE process runtime. Registry and output live only in memory.

APIs run on one asyncio loop. Non-awaiting buffer/registry operations are atomic
on that loop; stdin and termination have per-record synchronization. There is
no global I/O lock and no attachment to caller-supplied OS PIDs.
"""

from __future__ import annotations

import asyncio
import codecs
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
from time import monotonic
from types import MappingProxyType
from typing import Literal, Mapping
from uuid import uuid4

from chat2local.runtime.config import ProcessConfig
from chat2local.runtime.shell import ResolvedShell, ShellResolver
from chat2local.runtime.workspace import WorkspaceManager

MAX_FINISHED_PROCESSES = 64
STDIN_RESPONSE_WAIT = 0.250
PIPE_READ_SIZE = 16384
WINDOWS_TASKKILL_TIMEOUT = 5.0
ProcessState = Literal["running", "exited", "terminated"]
logger = logging.getLogger(__name__)


class ProcessError(RuntimeError):
    """An expected subprocess/runtime failure, safe to map at a future Tool layer."""


class UnknownProcessError(ProcessError):
    pass


class StdinClosedError(ProcessError):
    pass


def build_process_environment(*, platform: str | None = None) -> dict[str, str]:
    platform = sys.platform if platform is None else platform
    environment = dict(os.environ)
    environment.update(NO_COLOR="1", COLORTERM="", GIT_PAGER="cat", GH_PAGER="cat")
    if platform == "win32":
        extensions = environment.get("PATHEXT")
        if not extensions:
            environment["PATHEXT"] = ".COM;.EXE;.BAT;.CMD"
        elif ".EXE" not in (part.strip().upper() for part in extensions.split(";")):
            environment["PATHEXT"] = extensions.rstrip(";") + ";.EXE"
    elif platform == "darwin" or platform.startswith("linux"):
        locale = "en_US.UTF-8" if platform == "darwin" else "C.UTF-8"
        environment.update(TERM="dumb", LANG=locale, LC_CTYPE=locale, LC_ALL=locale, PAGER="cat")
    return environment


@dataclass(frozen=True)
class StreamOutput:
    text: str
    retained_start: int
    retained_end: int
    read_cursor: int
    output_dropped: bool
    has_more: bool


class OutputBuffer:
    """Byte-budgeted canonical UTF-8 text with absolute offsets and one cursor."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("Buffer limit must be positive")
        self.limit = limit
        self._data = bytearray()
        self.retained_start = 0
        self.retained_end = 0
        self.read_cursor = 0

    @property
    def retained_bytes(self) -> int:
        return len(self._data)

    def append(self, text: str) -> None:
        data = text.encode("utf-8")
        self._data.extend(data)
        self.retained_end += len(data)
        excess = len(self._data) - self.limit
        if excess > 0:
            while excess < len(self._data) and self._data[excess] & 0xC0 == 0x80:
                excess += 1
            del self._data[:excess]
            self.retained_start += excess

    def read(self, limit: int) -> StreamOutput:
        if limit < 0:
            raise ValueError("Read budget must be nonnegative")
        dropped = self.read_cursor < self.retained_start
        start = max(self.read_cursor, self.retained_start)
        begin = start - self.retained_start
        end = min(begin + limit, len(self._data))
        while end < len(self._data) and end > begin and self._data[end] & 0xC0 == 0x80:
            end -= 1
        text = bytes(self._data[begin:end]).decode("utf-8")
        self.read_cursor = self.retained_start + end
        return StreamOutput(text, self.retained_start, self.retained_end, self.read_cursor,
                            dropped, self.read_cursor < self.retained_end)


@dataclass(frozen=True)
class ProcessStatus:
    process_id: str
    state: ProcessState
    exit_code: int | None
    draining: bool


@dataclass(frozen=True)
class ProcessOutput:
    status: ProcessStatus
    stdout: StreamOutput
    stderr: StreamOutput


@dataclass(frozen=True)
class TerminationResult:
    outcome: Literal["terminated", "already_exited"]
    status: ProcessStatus


@dataclass
class ProcessRecord:
    process_id: str
    process: asyncio.subprocess.Process
    stdout: OutputBuffer
    stderr: OutputBuffer
    started_at: float
    finished_at: float | None = None
    termination_sent: bool = False
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    force_requested: asyncio.Event = field(default_factory=asyncio.Event)
    stdin_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    signal_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    signal_finished: asyncio.Event = field(default_factory=asyncio.Event)
    drain_tasks: tuple[asyncio.Task, ...] = ()
    watch_task: asyncio.Task | None = None
    termination_task: asyncio.Task | None = None
    io_error: Exception | None = None

    def __post_init__(self) -> None:
        self.signal_finished.set()

    @property
    def state(self) -> ProcessState:
        if self.process.returncode is None:
            return "running"
        return "terminated" if self.termination_sent else "exited"

    def status(self) -> ProcessStatus:
        return ProcessStatus(self.process_id, self.state, self.process.returncode, not self.finished.is_set())


class ProcessManager:
    def __init__(self, config: ProcessConfig | None = None, *, shell: ResolvedShell | None = None) -> None:
        self.config = config or ProcessConfig()
        self.shell = shell or ShellResolver(self.config.shell).resolve()
        self._records: dict[str, ProcessRecord] = {}
        self._spawning: set[asyncio.Task] = set()
        self._closing = False
        self._shutdown_task: asyncio.Task | None = None

    @property
    def records(self) -> Mapping[str, ProcessRecord]:
        return MappingProxyType(self._records)

    def cleanup(self) -> None:
        now = monotonic()
        finished = sorted((record for record in self._records.values() if record.finished_at is not None),
                          key=lambda record: record.finished_at)
        expired = {record.process_id for record in finished
                   if now - record.finished_at > self.config.finished_retention}
        remaining = [record for record in finished if record.process_id not in expired]
        expired.update(record.process_id for record in remaining[:-MAX_FINISHED_PROCESSES])
        for process_id in expired:
            del self._records[process_id]

    def _get(self, process_id: str) -> ProcessRecord:
        self.cleanup()
        try:
            return self._records[process_id]
        except KeyError:
            raise UnknownProcessError("Unknown managed process_id") from None

    def status(self, process_id: str) -> ProcessStatus:
        return self._get(process_id).status()

    async def spawn(
        self, command: str, *, workspace: WorkspaceManager, cwd: str | Path = ".",
    ) -> ProcessRecord:
        if self._closing:
            raise ProcessError("ProcessManager is shut down")
        target = workspace.resolve_path(cwd)
        if not target.is_dir():
            raise ProcessError("Process cwd must be an existing directory")
        argv = self.shell.argv(command)
        # A cancelled caller cannot abandon a child between OS spawn and registry
        # insertion. Shutdown also waits for every in-flight spawn to be enrolled.
        task = asyncio.create_task(self._spawn(argv, target), name="chat2local-process-spawn")
        self._spawning.add(task)
        task.add_done_callback(self._spawn_done)
        return await asyncio.shield(task)

    def _spawn_done(self, task: asyncio.Task) -> None:
        self._spawning.discard(task)
        if not task.cancelled():
            try:
                task.result()
            except ProcessError:
                logger.error("Managed process spawn failed (ProcessError)")
            except Exception:
                logger.exception("Unexpected managed process spawn failure")

    async def _spawn(self, argv: tuple[str, ...], cwd: Path) -> ProcessRecord:
        if self._closing:
            raise ProcessError("ProcessManager is shut down")
        self.cleanup()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=cwd, env=build_process_environment(), **options,
            )
        except OSError as error:
            raise ProcessError(f"Could not spawn process: {error.strerror or type(error).__name__}") from error
        record = ProcessRecord(str(uuid4()), process, OutputBuffer(self.config.stdout_buffer_limit),
                               OutputBuffer(self.config.stderr_buffer_limit), monotonic())
        self._records[record.process_id] = record
        record.drain_tasks = (
            asyncio.create_task(self._drain(process.stdout, record.stdout), name="chat2local-stdout-drain"),
            asyncio.create_task(self._drain(process.stderr, record.stderr), name="chat2local-stderr-drain"),
        )
        record.watch_task = asyncio.create_task(self._watch(record), name="chat2local-process-watch")
        return record

    async def _drain(self, reader: asyncio.StreamReader, output: OutputBuffer) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while data := await reader.read(PIPE_READ_SIZE):
            output.append(decoder.decode(data, final=False))
        output.append(decoder.decode(b"", final=True))

    async def _discard_failed_stream(self, reader: asyncio.StreamReader) -> None:
        # The failure is already recorded. Drain remaining bytes so pipe closure
        # and process reaping can finish even if the original reader task failed.
        try:
            while await reader.read(PIPE_READ_SIZE):
                pass
        except OSError:
            pass

    async def _watch(self, record: ProcessRecord) -> None:
        # returncode may become available before wait()/pipe EOF; status() reports
        # actual OS exit immediately, while retention starts only after draining.
        exit_wait = asyncio.create_task(record.process.wait(), name="chat2local-exit-wait")
        try:
            await asyncio.wait((exit_wait, *record.drain_tasks), return_when=asyncio.FIRST_EXCEPTION)
            failed = False
            for index, task in enumerate(record.drain_tasks):
                if task.done() and not task.cancelled() and task.exception() is not None:
                    failed = True
                    error = task.exception()
                    record.io_error = (
                        ProcessError(f"Process output I/O failure: {error.strerror}")
                        if isinstance(error, OSError) else error
                    )
                    reader = record.process.stdout if index == 0 else record.process.stderr
                    discard = asyncio.create_task(self._discard_failed_stream(reader), name="chat2local-failed-drain")
                    record.drain_tasks = (*record.drain_tasks, discard)
            # Keep the watcher alive if signaling fails, so a later terminate or
            # shutdown can still reap the managed child; APIs report the error.
            if failed and record.process.returncode is None:
                try:
                    record.termination_sent |= await self._signal_tree(record, force=True)
                except ProcessError as signal_error:
                    record.io_error = signal_error
            await exit_wait
            await asyncio.gather(*record.drain_tasks, return_exceptions=True)
        finally:
            if not exit_wait.done():
                exit_wait.cancel()
            await asyncio.gather(exit_wait, return_exceptions=True)
            try:
                record.process.stdin.close()
                await record.process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except OSError as error:
                record.io_error = ProcessError(f"Process stdin close I/O failure: {error.strerror}")
            finally:
                await record.signal_finished.wait()
                if record.process.returncode is not None:
                    record.finished_at = monotonic()
                    record.finished.set()
                self.cleanup()

    def read_output(self, process_id: str, *, limit: int | None = None) -> ProcessOutput:
        record = self._get(process_id)
        if record.io_error is not None:
            raise record.io_error
        budget = self.config.response_output_limit if limit is None else limit
        if budget < 4:
            raise ValueError("Response budget must be at least 4 UTF-8 bytes")
        stdout = record.stdout.read(budget)
        stderr = record.stderr.read(budget - len(stdout.text.encode("utf-8")))
        return ProcessOutput(record.status(), stdout, stderr)

    async def write_stdin(self, process_id: str, text: str) -> None:
        record = self._get(process_id)
        data = text.encode("utf-8")
        async with record.stdin_lock:
            if record.process.returncode is not None or record.process.stdin.is_closing():
                raise StdinClosedError("Managed process stdin is closed")
            try:
                record.process.stdin.write(data)
                await record.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                raise StdinClosedError("Managed process stdin is closed") from None
            except OSError as error:
                raise ProcessError(f"Process stdin I/O failure: {error.strerror}") from error

    async def close_stdin(self, process_id: str) -> None:
        record = self._get(process_id)
        async with record.stdin_lock:
            record.process.stdin.close()
            try:
                await record.process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    async def wait(self, process_id: str, *, timeout: float | None = None) -> ProcessStatus:
        record = self._get(process_id)
        if record.io_error is not None:
            raise record.io_error
        timeout = self.config.foreground_timeout if timeout is None else timeout
        if timeout <= 0:
            raise ValueError("Wait timeout must be positive")
        try:
            await asyncio.wait_for(record.finished.wait(), timeout)
        except TimeoutError:
            if record.io_error is not None:
                raise record.io_error
            return record.status()
        if record.io_error is not None:
            raise record.io_error
        # Propagate programmer errors from the watcher instead of hiding them.
        await asyncio.shield(record.watch_task)
        return record.status()

    async def terminate(self, process_id: str, *, force: bool = False) -> TerminationResult:
        record = self._get(process_id)
        return await self._terminate_record(record, force=force)

    async def _terminate_record(self, record: ProcessRecord, *, force: bool = False) -> TerminationResult:
        if record.termination_task is not None and not record.termination_task.done():
            if force:
                record.force_requested.set()
            return await asyncio.shield(record.termination_task)
        if record.process.returncode is not None:
            return TerminationResult("already_exited", record.status())
        if force:
            record.force_requested.set()
        if record.termination_task is None or (
            record.termination_task.done() and record.termination_task.exception()
        ):
            record.termination_task = asyncio.create_task(self._terminate(record), name="chat2local-process-terminate")
        return await asyncio.shield(record.termination_task)

    async def _signal_tree(self, record: ProcessRecord, *, force: bool) -> bool:
        if os.name != "nt":
            try:
                os.killpg(record.process.pid, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                return False
            except OSError as error:
                raise ProcessError(f"Could not signal process group: {error.strerror}") from error
            record.termination_sent = True
            return True
        async with record.signal_lock:
            record.signal_finished.clear()
            try:
                return await self._windows_signal_tree(record, force=force)
            finally:
                record.signal_finished.set()

    async def _windows_signal_tree(self, record: ProcessRecord, *, force: bool) -> bool:
        if record.process.returncode is not None:
            return False  # never taskkill a finished/recycled OS PID
        executable = shutil.which("taskkill.exe")
        if executable is None:
            raise ProcessError("taskkill.exe is unavailable")
        # CREATE_NO_WINDOW has no supported graceful termination path in V0.1.
        arguments = [executable, "/PID", str(record.process.pid), "/T", "/F"]
        try:
            helper = await asyncio.create_subprocess_exec(
                *arguments, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as error:
            raise ProcessError(f"Could not start taskkill: {error.strerror}") from error
        try:
            await asyncio.wait_for(helper.wait(), WINDOWS_TASKKILL_TIMEOUT)
        except TimeoutError:
            raise ProcessError("taskkill timed out") from None
        finally:
            if helper.returncode is None:
                helper.kill()
                await helper.wait()
        if helper.returncode != 0 and record.process.returncode is None:
            raise ProcessError(f"taskkill /T /F failed with exit code {helper.returncode}")
        if helper.returncode == 0:
            record.termination_sent = True
        return helper.returncode == 0

    async def _terminate(self, record: ProcessRecord) -> TerminationResult:
        if record.process.returncode is not None:
            return TerminationResult("already_exited", record.status())
        # Windows sends one forced tree termination; only POSIX waits/escalates.
        forced_initially = os.name == "nt" or record.force_requested.is_set()
        sent = await self._signal_tree(record, force=forced_initially)
        record.termination_sent |= sent
        if os.name != "nt" and not record.force_requested.is_set():
            finished = asyncio.create_task(record.finished.wait())
            force = asyncio.create_task(record.force_requested.wait())
            try:
                await asyncio.wait((finished, force), timeout=self.config.terminate_grace_period,
                                   return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (finished, force):
                    task.cancel()
                await asyncio.gather(finished, force, return_exceptions=True)
        if not record.finished.is_set() and not forced_initially:
            sent = await self._signal_tree(record, force=True)
            record.termination_sent |= sent
        await record.finished.wait()
        await asyncio.shield(record.watch_task)
        if record.io_error is not None:
            raise record.io_error
        return TerminationResult("terminated" if record.termination_sent else "already_exited", record.status())

    async def shutdown(self) -> None:
        self._closing = True
        if self._shutdown_task is None or (self._shutdown_task.done() and self._shutdown_task.exception()):
            self._shutdown_task = asyncio.create_task(self._shutdown(), name="chat2local-process-shutdown")
        await asyncio.shield(self._shutdown_task)

    async def _shutdown(self) -> None:
        await asyncio.gather(*tuple(self._spawning), return_exceptions=True)
        running = [record for record in self._records.values() if record.process.returncode is None]
        outcomes = await asyncio.gather(
            *(self._terminate_record(record) for record in running), return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        await asyncio.gather(*(record.watch_task for record in self._records.values()))
        await asyncio.gather(*(record.termination_task for record in self._records.values()
                               if record.termination_task is not None))
        errors = [record.io_error for record in self._records.values() if record.io_error is not None]
        self._records.clear()
        if errors:
            raise errors[0]
