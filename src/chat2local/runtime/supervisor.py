"""Thin Core lifecycle coordinator; business cleanup remains owned by the Core."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from dataclasses import asdict, replace
import errno
import logging
from pathlib import Path
import signal
import socket
import sys
import threading

import uvicorn

from chat2local.runtime import config
from chat2local.runtime.control import ControlServer
from chat2local.runtime.instance import InstanceLock, RuntimeDescriptor, RuntimeManagementError

logger = logging.getLogger(__name__)


class CoreServer(uvicorn.Server):
    _shutdown_started = False
    on_started: Callable[[], None] | None = None

    @contextmanager
    def capture_signals(self):
        # The supervisor owns signals across IPC startup and the whole Core life.
        yield

    async def startup(self, sockets=None):
        try:
            await super().startup(sockets=sockets)
            if self.started and self.on_started is not None:
                self.on_started()
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
                 *, state: Callable[[], str], directory: Path | None = None,
                 devices: Callable[[], dict] | None = None,
                 browser: Callable[[], dict] | None = None) -> None:
        self.mode = mode
        self.device_id = device_id
        self.workspace = workspace
        self.directory = config.user_data_directory() if directory is None else directory
        self.state = state
        self.descriptor = None
        self.stopping = False
        self._shutdown = asyncio.Event()
        self.control = ControlServer(self.status, self.request_stop, devices, browser)
        self.lock = InstanceLock(self.directory)
        # Optional local HTTP listener. It shares this supervisor's signals,
        # instance lock and shutdown; the Core's own cleanup stays unchanged.
        self.management: CoreServer | None = None
        self.management_ports: tuple[int, ...] | None = None

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
        async def execute(operation):
            # SystemExit from Uvicorn startup must return through our finally,
            # rather than escaping a child task and aborting the asyncio runner.
            try:
                await operation()
            except BaseException as error:
                return error
            return None
        task = asyncio.create_task(execute(core), name="chat2local-core")
        web = (asyncio.create_task(self._run_management(), name="chat2local-management")
               if self.management is not None else None)
        shutdown = asyncio.create_task(self._shutdown.wait())
        try:
            await asyncio.wait((task, shutdown), return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.request_stop()
            if self.management is not None:
                self.management.should_exit = True
            if not task.done():
                stop_core(task)
            shutdown.cancel()
            error = await _settle(task)
            if web:
                await _settle(web)
            await _settle(asyncio.gather(shutdown, return_exceptions=True))
        if error is not None and not isinstance(error, asyncio.CancelledError):
            raise error

    async def _run_management(self) -> None:
        """Try bounded startup binds; never restart a failed running listener."""
        server = self.management
        listener = None
        try:
            if self.management_ports is None:
                await server.serve()
            else:
                for index, port in enumerate(self.management_ports):
                    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    try:
                        if sys.platform != "win32":
                            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        candidate.bind(("127.0.0.1", port))
                        candidate.listen()
                        candidate.setblocking(False)
                    except OSError as error:
                        candidate.close()
                        if error.errno != errno.EADDRINUSE and getattr(error, "winerror", None) != 10048:
                            raise
                        if index + 1 == len(self.management_ports):
                            raise OSError(errno.EADDRINUSE, "All candidate Web UI ports are in use") from error
                        logger.warning("Web UI port %s is in use; trying %s", port, self.management_ports[index + 1])
                    else:
                        listener = candidate
                        server.config.port = port
                        break
                if listener is None:
                    raise OSError(errno.EADDRINUSE, "No candidate Web UI ports are available")
                await server.serve(sockets=[listener])
        except asyncio.CancelledError:
            return
        except BaseException as error:
            logger.error("Local Web UI at http://127.0.0.1:%s/ failed (%s: %s); Core continues running",
                         server.config.port, type(error).__name__, error)
        else:
            if not self.stopping:
                logger.error("Local Web UI at http://127.0.0.1:%s/ %s; Core continues running",
                             server.config.port, "exited unexpectedly" if server.started else "failed to start; see server errors")
        finally:
            if listener is not None:
                listener.close()

    async def _cleanup(self) -> None:
        try:
            await self.control.close()
        finally:
            if self.descriptor is not None:
                try:
                    self.descriptor.remove(self.directory)
                except OSError:
                    logger.error("Could not remove current runtime descriptor")
