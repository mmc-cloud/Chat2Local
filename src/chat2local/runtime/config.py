from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

DEFAULT_CONFIG_PATH = Path.home() / ".chat2local" / "config.yaml"

DEFAULT_MAX_LINES = 1000
DEFAULT_MAX_BYTES = 64 * 1024
DEFAULT_MAX_RESULTS = 100
HARD_MAX_RESULTS = 500
DEFAULT_TIMEOUT = 10.0
DEFAULT_SEARCH_MATCH_TEXT_CHARS = 2000
DEFAULT_SEARCH_RESULTS_BYTES = 64 * 1024


class ConfigError(ValueError):
    pass


# unknown keys are rejected, so a typo in config.yaml cannot silently keep a default
class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadConfig(_ConfigModel):
    max_lines: int = Field(default=DEFAULT_MAX_LINES, ge=1)
    max_bytes: int = Field(default=DEFAULT_MAX_BYTES, ge=1)


class SearchConfig(_ConfigModel):
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, ge=1, le=HARD_MAX_RESULTS)
    timeout: float = Field(default=DEFAULT_TIMEOUT, gt=0)


class SecurityConfig(_ConfigModel):
    allowed_roots: list[str] = Field(default_factory=list)


ShellName = Literal["auto", "pwsh", "powershell", "powershell.exe", "cmd", "cmd.exe", "bash", "sh", "zsh"]


class ProcessConfig(_ConfigModel):
    shell: ShellName = "auto"
    foreground_timeout: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    stdout_buffer_limit: int = Field(default=1048576, gt=0, strict=True)
    stderr_buffer_limit: int = Field(default=1048576, gt=0, strict=True)
    # A response must accommodate at least one complete UTF-8 scalar value.
    response_output_limit: int = Field(default=32768, ge=4, strict=True)
    terminate_grace_period: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    finished_retention: float = Field(default=600.0, gt=0, allow_inf_nan=False)


class HubConfig(_ConfigModel):
    device_id: str | None = None
    host: str | None = None
    port: int | None = None
    token_file: str | None = None


class AuthConfig(_ConfigModel):
    mode: Literal["none", "oauth"] = "none"
    provider: Literal["workos"] | None = None
    issuer_url: str | None = Field(default=None, strict=True)
    resource_server_url: str | None = Field(default=None, strict=True)

    @field_validator("issuer_url", "resource_server_url")
    @classmethod
    def valid_https_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            if any(char.isspace() or not char.isprintable() for char in value) or "\\" in value:
                raise ValueError
            parsed = urlsplit(value)
            AnyHttpUrl(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                    or parsed.query or parsed.fragment or parsed.port == 0):
                raise ValueError
        except ValueError:
            raise ValueError("must be a valid HTTPS URL without credentials, query or fragment") from None
        # JWT issuer and audience matching must preserve the configured spelling.
        return value

    @field_validator("resource_server_url")
    @classmethod
    def fixed_mcp_path(cls, value: str | None) -> str | None:
        if value is not None and urlsplit(value).path != "/mcp":
            raise ValueError("path must be exactly /mcp")
        return value

    @model_validator(mode="after")
    def oauth_parameters(self) -> AuthConfig:
        if self.mode == "oauth":
            for name in ("provider", "issuer_url", "resource_server_url"):
                if getattr(self, name) is None:
                    raise ValueError(f"auth.{name} is required when auth.mode is oauth")
        return self


class AgentConfig(_ConfigModel):
    device_id: str | None = None
    hub_url: str | None = None
    token_file: str | None = None
    proxy: str = Field(default="system", strict=True)

    @field_validator("proxy")
    @classmethod
    def valid_proxy(cls, value: str) -> str:
        return validate_agent_proxy(value)


def validate_agent_proxy(value: str) -> str:
    if value in ("system", "direct"):
        return value
    try:
        if not isinstance(value, str) or not value or any(char.isspace() or not char.isprintable() for char in value):
            raise ValueError
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https", "socks4", "socks4a", "socks5", "socks5h") or not parsed.hostname:
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError
        if parsed.username is not None and parsed.password is None:
            raise ValueError
    except ValueError:
        raise ValueError("agent.proxy must be system, direct or a valid HTTP/HTTPS/SOCKS proxy URL") from None
    return value


def validation_message(error: ValidationError) -> str:
    """Keep field locations and reasons without echoing input values or context."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in error.errors(include_input=False, include_context=False, include_url=False)
    )


class AppConfig(_ConfigModel):
    read: ReadConfig = Field(default_factory=ReadConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    process: ProcessConfig = Field(default_factory=ProcessConfig)
    hub: HubConfig = Field(default_factory=HubConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)


def load_config(
    path: Path | str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> AppConfig:
    """Build the AppConfig from built-in defaults, the user config file, then explicit CLI values.

    ``path`` defaults to ``~/.chat2local/config.yaml`` and is optional: a missing
    default file means "no user configuration". A missing file named explicitly by
    the caller is an error, so a typo cannot silently fall back to the defaults.

    ``overrides`` holds only the values the caller passed on the command line, so
    ``None`` entries are ignored and never hide a configured value.
    """

    if path is None:
        config_path: Path | None = DEFAULT_CONFIG_PATH
        missing_is_error = False
    else:
        config_path = Path(path).expanduser()
        missing_is_error = True

    data: dict[str, Any] = {}

    if config_path is not None:
        if config_path.exists():
            data = _read_config_file(config_path)
        elif missing_is_error:
            raise ConfigError(f"Config file does not exist: {config_path}")

    merged = _merge(data, _without_none(overrides or {}))

    try:
        return AppConfig.model_validate(merged)
    except ValidationError as error:
        raise ConfigError(f"Invalid config: {validation_message(error)}") from None


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"Could not read config file: {path}") from error

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        raise ConfigError(f"Invalid YAML in {path}{location}") from None

    if data is None:
        return {}

    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a mapping: {path}")

    return data


def _merge(base: Mapping[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    """Merge nested mappings one level at a time so partial overrides keep sibling values."""

    merged: dict[str, Any] = dict(base)

    for key, value in extra.items():
        current = merged.get(key)

        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _merge(current, value)
        else:
            merged[key] = value

    return merged


def _without_none(values: Mapping[str, Any]) -> dict[str, Any]:
    """Drop unset CLI values, keeping the nested structure for the merge."""

    return {
        key: _without_none(value) if isinstance(value, Mapping) else value
        for key, value in values.items()
        if value is not None
    }
