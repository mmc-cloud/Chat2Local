"""Route by device only; tool algorithms remain in the local dispatcher."""

from typing import Any

from chat2local.device.registry import DeviceError, DeviceInfo, DeviceRegistry, REMOTE_REQUEST_TIMEOUT
from chat2local.dispatch.local import LocalToolDispatcher


class DeviceRouter:
    def __init__(
        self, device_id: str, local: LocalToolDispatcher,
        registry: DeviceRegistry | None = None, *, timeout: float = REMOTE_REQUEST_TIMEOUT,
    ) -> None:
        # Apply the same identity validation to local and remote instances.
        DeviceInfo(device_id=device_id, kind="local", online=True, tools=list(local.tools))
        if registry is not None and registry.local_device_id != device_id:
            raise ValueError("Registry local device ID must match the Router")
        self.device_id = device_id
        self.local = local
        self.registry = registry
        self.timeout = timeout

    async def execute(
        self, tool_name: str, arguments: dict[str, Any], device: str | None = None,
    ) -> dict[str, Any]:
        if device is None or device == self.device_id:
            return await self.local.execute(tool_name, arguments)
        if self.registry is None:
            raise DeviceError(f"Device unavailable in standalone mode: {device}")
        return await self.registry.get(device).execute(tool_name, arguments, self.timeout)

    def list_devices(self) -> dict[str, Any]:
        devices = [DeviceInfo(
            device_id=self.device_id, kind="local", online=True, tools=list(self.local.tools),
        )]
        if self.registry is not None:
            devices.extend(
                DeviceInfo(device_id=session.device_id, kind="remote", online=True,
                           tools=list(session.capabilities))
                for session in self.registry.sessions.values() if not session.closed
            )
        return {"devices": [device.model_dump() for device in devices]}
