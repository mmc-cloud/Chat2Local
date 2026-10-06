"""Verified runtime discovery and independent Core lifecycle client."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chat2local.runtime import config
from chat2local.runtime.control import (
    CONTROL_TIMEOUT,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
)
from chat2local.runtime.workspace import WorkspaceError, WorkspaceManager


class ManagementError(RuntimeError):
    """A fixed or Core-validated, user-readable diagnostic."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ControlAddress(_StrictModel):
    transport: Literal["tcp"]
    host: Literal["127.0.0.1"]
    port: int = Field(ge=1, le=65535)


class CoreStatus(_StrictModel):
    instance_id: str = Field(min_length=1, max_length=128)
    pid: int = Field(gt=0)
    mode: Literal["standalone", "hub", "agent"]
    device_id: str = Field(min_length=1, max_length=128)
    workspace: str
    started_at: str
    version: str
    state: str


class Descriptor(_StrictModel):
    schema_version: Literal[1]
    instance_id: str = Field(min_length=1, max_length=128)
    pid: int = Field(gt=0)
    mode: Literal["standalone", "hub", "agent"]
    device_id: str = Field(min_length=1, max_length=128)
    workspace: str
    started_at: str
    version: str
    control: ControlAddress


class RuntimeClient:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        timeout: float = 20,
        control_timeout: float = CONTROL_TIMEOUT,
    ) -> None:
        self.directory = (
            config.user_data_directory() if directory is None else directory
        )
        self.timeout = timeout
        self.control_timeout = control_timeout
        self.transition: str | None = None
        self.error: str | None = None
        self._failed_phase: str | None = None
        self._operation = threading.Lock()
        self._child: subprocess.Popen | None = None

    def _descriptor(self) -> Descriptor | None:
        try:
            with (self.directory / "runtime.json").open("rb") as handle:
                raw = handle.read(MAX_RESPONSE_BYTES + 1)
        except FileNotFoundError:
            return None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ManagementError("Runtime information is stale")
        try:
            return Descriptor.model_validate_json(raw)
        except ValidationError:
            raise ManagementError("Runtime information is stale") from None

    async def request(self, descriptor: Descriptor, method: str) -> dict:
        identifier = str(uuid4())
        payload = json.dumps({"id": identifier, "method": method}).encode() + b"\n"
        if len(payload) > MAX_REQUEST_BYTES:
            raise ManagementError("Invalid control request")
        writer = None
        try:
            async with asyncio.timeout(self.control_timeout):
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", descriptor.control.port, limit=MAX_RESPONSE_BYTES
                )
                writer.write(payload)
                await writer.drain()
                raw = await reader.readline()
                if not raw.endswith(b"\n") or len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError
                response = json.loads(raw)
                if isinstance(response, dict) and response.get("ok") is False:
                    if (
                        set(response) != {"id", "ok", "error"}
                        or response.get("id") not in (identifier, None)
                        or not isinstance(response.get("error"), str)
                    ):
                        raise ValueError
                    messages = {
                        "devices_unavailable": "Device snapshot unavailable in this Core; restart after updating",
                        "response_too_large": "Core response exceeds the local control size limit",
                        "invalid_request": "Core does not support this control request; restart after updating",
                    }
                    raise ManagementError(
                        messages.get(
                            response.get("error"),
                            "Local Core control unavailable; see logs",
                        )
                    )
                if (
                    not isinstance(response, dict)
                    or response.get("id") != identifier
                    or response.get("ok") is not True
                    or set(response) != {"id", "ok", "result"}
                    or not isinstance(response["result"], dict)
                ):
                    raise ValueError
                return response["result"]
        except (OSError, TimeoutError, ValueError, UnicodeDecodeError):
            raise ManagementError("Local Core control unavailable; see logs") from None
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), self.control_timeout)
                except (OSError, TimeoutError):
                    pass

    async def discover(self) -> dict:
        try:
            descriptor = self._descriptor()
            if descriptor is None:
                return {"lifecycle": "Stopped", "core": None}
            ping = await self.request(descriptor, "ping")
            if ping.get("instance_id") != descriptor.instance_id:
                raise ManagementError("Runtime information is stale")
            status = CoreStatus.model_validate(await self.request(descriptor, "status"))
            if status.instance_id != descriptor.instance_id:
                raise ManagementError("Runtime information is stale")
            return {"lifecycle": "Running", "core": status.model_dump()}
        except (ManagementError, ValidationError):
            return {
                "lifecycle": "Stale",
                "core": None,
                "message": "Runtime information is stale",
            }
        except OSError:
            return {
                "lifecycle": "Error",
                "core": None,
                "message": "Could not read runtime information",
            }

    async def status(self) -> dict:
        snapshot = await self.discover()
        if snapshot["lifecycle"] == "Running" and self._failed_phase == "Starting":
            self.error = None
        if self.transition:
            snapshot["lifecycle"] = self.transition
        elif self.error:
            snapshot.update(lifecycle="Error", message=self.error)
        return snapshot

    def _args(self, mode: str, workspace: str) -> list[str]:
        if mode not in ("standalone", "hub", "agent"):
            raise ManagementError("Invalid Core mode")
        prefix = (
            [sys.executable, "--core"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-m", "chat2local.main"]
        )
        return [
            *prefix,
            *([] if mode == "standalone" else [mode]),
            "--workspace",
            workspace,
        ]

    async def _start(self, mode: str, workspace: str) -> dict:
        if (await self.discover())["lifecycle"] == "Running":
            raise ManagementError("Core is already running")
        if self._child is not None and self._child.poll() is None:
            raise ManagementError("A Core launch is still pending; see logs")
        self._args(mode, workspace)
        try:
            previous = self._descriptor()
        except ManagementError:
            previous = None
        settings = config.load_config()
        selected = WorkspaceManager(
            workspace, allowed_roots=settings.security.allowed_roots or None
        )
        args = self._args(mode, str(selected.root))
        # Popen has no transport destructor that kills its child when a GUI
        # request's asyncio loop closes. No pipe depends on the GUI staying alive.
        options = (
            {"creationflags": subprocess.CREATE_NO_WINDOW}
            if sys.platform == "win32"
            else {"start_new_session": True}
        )
        self._child = await asyncio.to_thread(
            subprocess.Popen,
            args,
            cwd=selected.root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
        deadline = asyncio.get_running_loop().time() + self.timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._child.poll() is not None:
                raise ManagementError(
                    "Core failed to start; check configuration and view logs"
                )
            snapshot = await self.discover()
            core = snapshot["core"]
            if (
                core
                and core["mode"] == mode
                and core["workspace"] == str(selected.root)
                and (previous is None or core["instance_id"] != previous.instance_id)
                and core["state"] != "starting"
            ):
                return snapshot
            await asyncio.sleep(0.15)
        raise ManagementError(
            "Core startup timed out; a launch may still be pending. See logs"
        )

    async def _stop(self) -> dict:
        snapshot = await self.discover()
        if snapshot["lifecycle"] == "Stopped":
            return snapshot
        if snapshot["lifecycle"] != "Running":
            raise ManagementError(
                "Runtime information is stale; cannot safely stop Core"
            )
        descriptor = self._descriptor()
        if (
            descriptor is None
            or descriptor.instance_id != snapshot["core"]["instance_id"]
        ):
            raise ManagementError("Runtime changed; refresh before stopping")
        # Reverify immediately before sending the stop request.
        if (await self.request(descriptor, "ping")).get(
            "instance_id"
        ) != descriptor.instance_id:
            raise ManagementError("Runtime information is stale")
        accepted = await self.request(descriptor, "stop")
        if accepted != {"accepted": True}:
            raise ManagementError("Core did not acknowledge stop; see logs")
        deadline = asyncio.get_running_loop().time() + self.timeout
        while asyncio.get_running_loop().time() < deadline:
            current = await self.discover()
            if current["lifecycle"] in ("Stopped", "Stale") and (
                self._child is None or self._child.poll() is not None
            ):
                return {"lifecycle": "Stopped", "core": None}
            if (
                current["core"]
                and current["core"]["instance_id"] != descriptor.instance_id
            ):
                raise ManagementError(
                    "Another Core started while stopping; refresh status"
                )
            await asyncio.sleep(0.15)
        raise ManagementError("Core stop timed out; see logs")

    async def action(
        self, action: str, mode: str = "standalone", workspace: str = ""
    ) -> dict:
        if action not in ("start", "stop", "restart"):
            raise ManagementError("Invalid Core operation")
        if not self._operation.acquire(blocking=False):
            raise ManagementError("A Core operation is already in progress")
        self.error = None
        try:
            if action == "restart":
                snapshot = await self.discover()
                if snapshot["lifecycle"] != "Running":
                    raise ManagementError("Core must be running to restart")
                mode, workspace = (
                    snapshot["core"]["mode"],
                    snapshot["core"]["workspace"],
                )
            self.transition = "Starting" if action == "start" else "Stopping"
            if action in ("stop", "restart"):
                snapshot = await self._stop()
            if action in ("start", "restart"):
                self.transition = "Starting"
                snapshot = await self._start(mode, workspace)
            return snapshot
        except (ManagementError, config.ConfigError, WorkspaceError) as error:
            self.error = str(error)
            self._failed_phase = self.transition
            raise
        except Exception:
            self.error = "Internal GUI/Core error; see logs"
            self._failed_phase = self.transition
            raise
        finally:
            self.transition = None
            self._operation.release()

    async def devices(self) -> dict:
        snapshot = await self.discover()
        if snapshot["lifecycle"] == "Stopped":
            return {"devices": [], "mode": None}
        if snapshot["lifecycle"] != "Running":
            raise ManagementError("Runtime information is stale")
        descriptor = self._descriptor()
        if (
            descriptor is None
            or descriptor.instance_id != snapshot["core"]["instance_id"]
        ):
            raise ManagementError("Runtime changed; refresh devices")
        result = await self.request(descriptor, "devices")
        # Do not pass unknown objects or extra fields through the GUI boundary.
        from chat2local.device.registry import DeviceInfo

        if set(result) != {"devices"} or not isinstance(result["devices"], list):
            raise ManagementError("Invalid device snapshot")
        devices = []
        for item in result["devices"]:
            if not isinstance(item, dict) or set(item) != {
                "device_id",
                "kind",
                "online",
                "tools",
            }:
                raise ManagementError("Invalid device snapshot")
            try:
                devices.append(
                    DeviceInfo.model_validate(item, strict=True).model_dump()
                )
            except ValidationError:
                raise ManagementError("Invalid device snapshot") from None
        return {"devices": devices, "mode": snapshot["core"]["mode"]}
