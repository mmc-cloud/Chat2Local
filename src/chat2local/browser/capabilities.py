"""Small in-memory capability catalogue; schemas and validation share models."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from chat2local.browser.bridge import BrowserBridge, CAPABILITY, failure


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CreateTabArguments(_Arguments):
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


class TabSelector(_Arguments):
    tab_id: int | None = Field(default=None, ge=0, le=2**53 - 1,
                               description="Native Chrome tab ID, not a ChatGPT browser ID; must be a JavaScript safe integer.")
    url: str | None = Field(default=None, min_length=1, max_length=8192, description="Exact current URL, including query/fragment.")
    title: str | None = Field(default=None, min_length=1, max_length=512, description="Exact case-sensitive tab title. Conditions are ANDed.")

    @model_validator(mode="after")
    def selected(self) -> "TabSelector":
        if all(value is None for value in (self.tab_id, self.url, self.title)):
            raise ValueError("At least one explicit tab condition is required")
        return self


class ListTabsArguments(_Arguments):
    tab: TabSelector | None = None
    limit: int = Field(default=20, ge=1, le=50)


class ResolveTabArguments(_Arguments):
    tab: TabSelector


class ElementTarget(_Arguments):
    role: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=256)
    aria_label: str | None = Field(default=None, min_length=1, max_length=256)
    label: str | None = Field(default=None, min_length=1, max_length=256)
    placeholder: str | None = Field(default=None, min_length=1, max_length=256)
    href: str | None = Field(default=None, min_length=1, max_length=8192,
                             description="Exact resolved absolute link URL returned by inspect; relative href strings are not resolved.")
    text: str | None = Field(default=None, min_length=1, max_length=256)
    id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def constrained(self) -> "ElementTarget":
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("At least one target condition is required")
        return self


class InspectArguments(ResolveTabArguments):
    target: ElementTarget | None = None
    limit: int = Field(default=20, ge=1, le=20)
    wait_ms: int = Field(default=0, ge=0, le=5000, description="Bounded wait for an element; never retries actions.")


class ClickArguments(ResolveTabArguments):
    target: ElementTarget
    wait_ms: int = Field(default=0, ge=0, le=5000)


class FillArguments(ClickArguments):
    value: str = Field(max_length=8192, description="Replacement text. Password and read-only fields are rejected.")


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
        for name, description, model in (
            (CAPABILITY, "Create a Chrome tab with an HTTP or HTTPS URL.", CreateTabArguments),
            ("browser.tabs.list", "List Chrome tabs; optional exact ID/URL/title filters use AND. Results are bounded.", ListTabsArguments),
            ("browser.tabs.resolve", "Resolve exactly one existing Chrome tab; ambiguous matches return candidates, never the first tab.", ResolveTabArguments),
            ("browser.page.inspect", "Inspect bounded semantic control summaries, including visible and hidden elements, in an authorized top-level page; no field values are returned.", InspectArguments),
            ("browser.page.click", "Click the unique visible semantic target on an authorized page; hidden matches are ignored. Synthetic click does not guarantee navigation or business success.", ClickArguments),
            ("browser.page.fill", "Replace text in the unique visible matching input/textarea using native setters and input/change events; hidden matches are ignored. Does not submit forms.", FillArguments),
        ):
            self.register(Capability(name, description, model, browser.execute,
                                     lambda name=name: browser.is_available(name)))

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
        return await entry.execute(name, validated.model_dump(exclude_none=True))
