from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

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


class AppConfig(_ConfigModel):
    read: ReadConfig = Field(default_factory=ReadConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    process: ProcessConfig = Field(default_factory=ProcessConfig)


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
        raise ConfigError(f"Invalid config: {error}") from error


def _read_config_file(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"Could not read config file: {path}") from error

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ConfigError(f"Invalid YAML in {path}: {error}") from error

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
