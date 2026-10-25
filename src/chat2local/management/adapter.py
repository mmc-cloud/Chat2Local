"""Compose existing Core capabilities, independent of any desktop framework."""

from __future__ import annotations
import hmac
from pathlib import Path

from chat2local.handoff.models import HandoffError
from chat2local.handoff.store import HandoffStore
from chat2local.management.logs import LogReader
from chat2local.management.runtime import RuntimeClient
from chat2local.management.runtime import ManagementError
from chat2local.management.credentials import CredentialStore
from chat2local.runtime import config


class CoreAdapter:
    def __init__(self, runtime: RuntimeClient | None = None, *, config_path: Path | None = None) -> None:
        self.runtime = runtime or RuntimeClient()
        self.config_path = config_path
        self.logs = LogReader(self.runtime.directory / "logs/chat2local.log")
        self._restart_instance: str | None = None
        self.credentials = CredentialStore(config_path=config_path)

    async def list_local_handoffs(self) -> dict:
        return await HandoffStore().list_local()

    async def get_local_handoff(self, handoff_id: str) -> dict:
        try:
            return await HandoffStore().get_local(handoff_id)
        except (HandoffError, OSError) as error:
            raise ManagementError(str(error)) from error

    async def save_local_handoff(
        self, handoff_id: str, title: str, summary: str, content: str, expected_revision: int,
    ) -> dict:
        try:
            return await HandoffStore().save_local(handoff_id, title, summary, content, expected_revision)
        except (ValueError, OSError) as error:
            raise ManagementError(str(error)) from error

    async def runtime_status(self) -> dict:
        result = await self.runtime.status()
        current = result["core"]
        if current is None or current["instance_id"] != self._restart_instance:
            self._restart_instance = None
        result["restart_required"] = self._restart_instance is not None
        return result

    def read_config(self) -> dict:
        persisted = config.read_persisted_config(self.config_path)
        effective = config.validate_persisted_config(persisted)
        return {
            "persisted": persisted,
            "effective": effective.model_dump(),
            "defaults": config.AppConfig().model_dump(),
        }

    def validate_config(self, candidate: dict) -> dict:
        settings = config.validate_persisted_config(candidate)
        if settings.browser.token_file:
            browser_path = Path(settings.browser.token_file).expanduser().absolute()
            for peer in (settings.hub, settings.agent):
                if not peer.token_file:
                    continue
                peer_path = Path(peer.token_file).expanduser().absolute()
                if browser_path == peer_path:
                    raise ManagementError("Browser token must be independent of Hub and Agent tokens")
                try:
                    browser_token = self.credentials.read("browser", str(browser_path))
                    shared_token = self.credentials.read("hub", str(peer_path))
                except ManagementError:
                    continue  # File availability remains a separately reported runtime concern.
                if hmac.compare_digest(browser_token.encode(), shared_token.encode()):
                    raise ManagementError("Browser token must be independent of Hub and Agent tokens")
        return {"valid": True}

    async def save_config(self, candidate: dict) -> dict:
        self.validate_config(candidate)
        before = await self.runtime.discover()
        config.save_persisted_config(candidate, self.config_path)
        if before["core"]:
            self._restart_instance = before["core"]["instance_id"]
        else:
            self._restart_instance = None
        return {
            **self.read_config(),
            "restart_required": self._restart_instance is not None,
        }

    def credential_info(self, purpose: str, path: str | None = None) -> dict:
        return self.credentials.info(purpose, path)

    def manage_token(self, purpose: str, path: str | None = None, value: str | None = None,
                     regenerate: bool = False, confirmed: bool = False) -> dict:
        return self.credentials.write(purpose, path, value=value, regenerate=regenerate, confirmed=confirmed)

    def token_for_copy(self, purpose: str, path: str | None = None) -> str:
        return self.credentials.read(purpose, path)

    async def browser_status(self) -> dict:
        try:
            saved = config.load_config(self.config_path).browser.model_dump()
        except config.ConfigError:
            return {"state": "configuration_error"}
        try:
            live = await self.runtime.browser()
        except ManagementError:
            return {"state": "unavailable"}
        if live is None:
            return {"state": "core_stopped" if saved["enabled"] else "disabled"}
        if live["config"] != saved or not live["credential_current"]:
            return {"state": "restart_required"}
        return {"state": live["state"]}
