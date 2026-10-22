"""Small in-memory capability catalogue; schemas and validation share models."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, field_validator

from chat2local.browser.bridge import BrowserBridge, CAPABILITY, failure


class CreateTabArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=1, max_length=8192, pattern=r"^https?://[^\s\\]+$",
                     description="Absolute HTTP or HTTPS URL without credentials, spaces or control characters.")

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            AnyHttpUrl(value)
            if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                    or parsed.port == 0 or any(not char.isprintable() for char in value)):
                raise ValueError
        except ValueError:
            raise ValueError("url must be an absolute HTTP(S) URL without credentials or control characters") from None
        return value


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    arguments: type[BaseModel]
    execute: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
    available: Callable[[], bool]


class CapabilityRegistry:
    def __init__(self, browser: BrowserBridge) -> None:
        self._entries: dict[str, Capability] = {}
        self.register(Capability(
            CAPABILITY, "Create a Chrome tab with an HTTP or HTTPS URL.",
            CreateTabArguments, browser.execute, lambda: browser.available,
        ))

    def register(self, capability: Capability) -> None:
        if not capability.name or capability.name in self._entries:
            raise ValueError("Invalid or duplicate capability name")
        self._entries[capability.name] = capability

    def discover(self, query: str | None = None) -> dict[str, Any]:
        keywords = (query or "").casefold().split()
        return {"ok": True, "result": {"capabilities": [
            {"name": entry.name, "description": entry.description,
             "input_schema": entry.arguments.model_json_schema(), "available": entry.available()}
            for entry in self._entries.values()
            if all(word in f"{entry.name} {entry.description}".casefold() for word in keywords)
        ]}}

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        entry = self._entries.get(name)
        if entry is None:
            return failure("unknown_capability", "Unknown capability; use capability_discover")
        try:
            validated = entry.arguments.model_validate(arguments)
        except ValidationError:
            return failure("invalid_arguments", "Arguments do not match the capability input schema or URL rules")
        if not entry.available():
            return failure("capability_unavailable", "Browser extension is not connected or capability is unavailable")
        return await entry.execute(name, validated.model_dump())
