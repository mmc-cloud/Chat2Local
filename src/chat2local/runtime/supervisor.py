"""Thin Core lifecycle coordinator; business cleanup remains owned by the Core."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import asdict, replace
import logging
from pathlib import Path
import signal
import threading

import uvicorn

from chat2local.runtime import config
from chat2local.runtime.control import ControlServer
from chat2local.runtime.instance import InstanceLock, RuntimeDescriptor, RuntimeManagementError

logger = logging.getLogger(__name__)


class CoreServer(uvicorn.Server):
    _shutdown_started = False

    @contextmanager
    def capture_signals(self):
        # The supervisor owns signals across IPC startup and the whole Core life.
        yield

    async def startup(self, sockets=None):
        try:
            await super().startup(sockets=sockets)
        except BaseException:
            # Uvicorn handles bind OSError itself, but e.g. an out-of-range
            # port can fail after lifespan startup without sending shutdown.
            finished = getattr(self.lifespan, "shutdown_event", None)
            if finished is not None and not finished.is_set():
                try:
                    await self.lifespan.shutdown()
                except BaseException:
                    logger.exception("Unexpected server startup cleanup failure")
            raise

    async def serve(self, sockets=None):
        main_failed = False
        try:
            await super().serve(sockets=sockets)
        except BaseException:
            main_failed = True
            raise
        finally:
            # Uvicorn's serve() has no finally around its main loop. A runtime
            # error must still send lifespan shutdown, using Uvicorn's cleanup.
            if self.started and not self._shutdown_started:
                try:
                    await self.shutdown(sockets=sockets)
                except BaseException:
                    logger.exception("Unexpected server shutdown failure")
                    if not main_failed:
                        raise

    async def shutdown(self, sockets=None):
        self._shutdown_started = True
        await super().shutdown(sockets=sockets)


async def _settle(task: asyncio.Task):
    """Repeated cancellation must not abandon Core/IPC cleanup or release early."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


class RuntimeSupervisor:
    def __init__(self, mode: str, device_id: str, workspace: Path,
                 *, state: Callable[[], str], directory: Path | None = None) -> None:
        self.mode = mode
        self.device_id = device_id
        self.workspace = workspace
        self.directory = config.user_data_directory() if directory is None else directory
        self.state = state
        self.descriptor = None
        self.stopping = False
        self._shutdown = asyncio.Event()
        self.control = ControlServer(self.status, self.request_stop)
        self.lock = InstanceLock(self.directory)

    def request_stop(self) -> None:
        if not self.stopping:
            self.stopping = True
            self._shutdown.set()

    def status(self) -> dict:
        data = asdict(self.descriptor)
        data.pop("control")
        data.pop("schema_version")
        data["state"] = "stopping" if self.stopping else self.state()
        return data

    @contextmanager
    def _signals(self):
        previous = {}
        if threading.current_thread() is threading.main_thread():
            loop = asyncio.get_running_loop()
            signals = [signal.SIGINT, signal.SIGTERM]
            if hasattr(signal, "SIGBREAK"):
                signals.append(signal.SIGBREAK)
            def stop(signum, frame):
                loop.call_soon_threadsafe(self.request_stop)
            try:
                for sig in signals:
                    previous[sig] = signal.signal(sig, stop)
                yield
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
        else:
            yield

    async def run(self, core: Callable[[], Awaitable[None]],
                  stop_core: Callable[[asyncio.Task], None]) -> None:
        try:
            self.lock.acquire()
        except OSError:
            raise RuntimeManagementError("Could not acquire Chat2Local runtime lock") from None
        try:
            with self._signals():
                main_failed = False
                try:
                    self.descriptor = RuntimeDescriptor.new(
                        self.mode, self.device_id, self.workspace, 0,
                    )
                    port = await self.control.start()
                    self.descriptor = replace(
                        self.descriptor, control={"transport": "tcp", "host": "127.0.0.1", "port": port},
                    )
                    self.descriptor.publish(self.directory)
                    logger.info("Core runtime published: mode=%s pid=%s", self.mode, self.descriptor.pid)
                    await self._run_core(core, stop_core)
                except BaseException:
                    main_failed = True
                    raise
                finally:
                    try:
                        await _settle(asyncio.create_task(self._cleanup()))
                    except BaseException:
                        logger.exception("Unexpected runtime management cleanup failure")
                        if not main_failed:
                            raise
        finally:
            self.lock.release()

    async def _run_core(self, core, stop_core) -> None:
        async def execute():
            # SystemExit from Uvicorn startup must return through our finally,
            # rather than escaping a child task and aborting the asyncio runner.
            try:
                await core()
            except BaseException as error:
                return error
            return None
        task = asyncio.create_task(execute(), name="chat2local-core")
        shutdown = asyncio.create_task(self._shutdown.wait())
        try:
            await asyncio.wait((task, shutdown), return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.request_stop()
            if not task.done():
                stop_core(task)
            shutdown.cancel()
            error = await _settle(task)
            await _settle(asyncio.gather(shutdown, return_exceptions=True))
        if error is not None and not isinstance(error, asyncio.CancelledError):
            raise error

    async def _cleanup(self) -> None:
        try:
            await self.control.close()
        finally:
            if self.descriptor is not None:
                try:
                    self.descriptor.remove(self.directory)
                except OSError:
                    logger.error("Could not remove current runtime descriptor")
