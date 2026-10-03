"""Process configuration is user policy, never persisted lifecycle state."""

import pytest
from pydantic import ValidationError

from chat2local.runtime.config import AppConfig, ConfigError, ProcessConfig, load_config
from test_config import write_config


def test_process_config_defaults():
    assert AppConfig().process.model_dump() == {
        "shell": "auto", "foreground_timeout": 10.0,
        "stdout_buffer_limit": 1048576, "stderr_buffer_limit": 1048576,
        "response_output_limit": 32768, "terminate_grace_period": 3.0, "finished_retention": 600.0,
    }


def test_partial_yaml_process_override_and_existing_merge(tmp_path):
    path = write_config(tmp_path, """read:
  max_lines: 7
search:
  max_results: 8
security:
  allowed_roots: ['/root/a']
process:
  foreground_timeout: 2.5
  stdout_buffer_limit: 99
""")
    config = load_config(path, {"search": {"timeout": 3}, "process": {"stderr_buffer_limit": 55, "shell": None}})
    assert config.read.max_lines == 7 and config.search.max_results == 8 and config.search.timeout == 3
    assert config.security.allowed_roots == ["/root/a"]
    assert config.process.foreground_timeout == 2.5 and config.process.stdout_buffer_limit == 99
    assert config.process.stderr_buffer_limit == 55 and config.process.shell == "auto"
    assert config.process.response_output_limit == 32768


@pytest.mark.parametrize("field", [
    "foreground_timeout", "stdout_buffer_limit", "stderr_buffer_limit", "response_output_limit",
    "terminate_grace_period", "finished_retention",
])
@pytest.mark.parametrize("value", [0, -1])
def test_nonpositive_process_values_rejected(field, value, tmp_path):
    with pytest.raises(ValidationError):
        ProcessConfig(**{field: value})
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, f"process:\n  {field}: {value}\n"))


@pytest.mark.parametrize("field", ["foreground_timeout", "terminate_grace_period", "finished_retention"])
@pytest.mark.parametrize("value", [float("inf"), float("nan")])
def test_times_must_be_finite(field, value):
    with pytest.raises(ValidationError):
        ProcessConfig(**{field: value})


@pytest.mark.parametrize("body", [
    "shell: wsl", "shell: fish", "shell: /missing/shell", "shell:", "shell: [bash]",
    "unknown_key: 1", "process_id: test", "registry: {}", "stdout_buffer: []",
    "stdout_buffer_limit: true", "stdout_buffer_limit: 1.5", "response_output_limit: 3",
])
def test_invalid_process_config_and_runtime_state_rejected(tmp_path, body):
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, f"process:\n  {body}\n"))


@pytest.mark.parametrize("shell", [
    "auto", "pwsh", "powershell", "powershell.exe", "cmd", "cmd.exe", "bash", "sh", "zsh",
])
def test_supported_shell_names(shell):
    assert ProcessConfig(shell=shell).shell == shell


def test_process_defaults_without_yaml_and_example(tmp_path, monkeypatch):
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "missing")
    assert load_config().process == ProcessConfig()
    assert load_config(overrides={"process": {"foreground_timeout": 4}}).process.foreground_timeout == 4
