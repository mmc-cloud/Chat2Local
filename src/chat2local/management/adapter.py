"""Compose existing Core capabilities, independent of any desktop framework."""

from __future__ import annotations

from chat2local.management.logs import LogReader
from chat2local.management.runtime import RuntimeClient
from chat2local.runtime import config


class CoreAdapter:
    def __init__(self, runtime: RuntimeClient | None = None) -> None:
        self.runtime = runtime or RuntimeClient()
        self.logs = LogReader(self.runtime.directory / "logs/chat2local.log")
        self._restart_instance: str | None = None

    async def runtime_status(self) -> dict:
        result = await self.runtime.status()
        current = result["core"]
        if current is None or current["instance_id"] != self._restart_instance:
            self._restart_instance = None
        result["restart_required"] = self._restart_instance is not None
        return result

    def read_config(self) -> dict:
        persisted = config.read_persisted_config()
        effective = config.validate_persisted_config(persisted)
        return {
            "persisted": persisted,
            "effective": effective.model_dump(),
            "defaults": config.AppConfig().model_dump(),
        }

    def validate_config(self, candidate: dict) -> dict:
        config.validate_persisted_config(candidate)
        return {"valid": True}

    async def save_config(self, candidate: dict) -> dict:
        config.validate_persisted_config(candidate)
        before = await self.runtime.discover()
        config.save_persisted_config(candidate)
        if before["core"]:
            self._restart_instance = before["core"]["instance_id"]
        else:
            self._restart_instance = None
        return {
            **self.read_config(),
            "restart_required": self._restart_instance is not None,
        }
