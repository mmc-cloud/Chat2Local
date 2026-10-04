from pathlib import Path

import pytest

from chat2local.runtime.config import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_RESULTS,
    DEFAULT_TIMEOUT,
    AppConfig,
    AgentConfig,
    HubConfig,
    ConfigError,
    SecurityConfig,
    load_config,
)


def write_config(directory: Path, text: str, name: str = "config.yaml") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)

    return path


def test_defaults_when_no_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")

    config = load_config()

    assert isinstance(config, AppConfig)
    assert config.read.max_lines == DEFAULT_MAX_LINES
    assert config.read.max_bytes == DEFAULT_MAX_BYTES
    assert config.search.max_results == DEFAULT_MAX_RESULTS
    assert config.search.timeout == DEFAULT_TIMEOUT


def test_default_config_path_is_the_documented_location() -> None:
    from chat2local.runtime.config import DEFAULT_CONFIG_PATH

    assert DEFAULT_CONFIG_PATH == Path.home() / ".chat2local" / "config.yaml"


def test_example_config_is_valid_and_matches_defaults() -> None:
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"

    assert example.exists()

    config = load_config(example)

    assert config == AppConfig()


def test_non_utf8_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_bytes("read:\n  max_lines: 1\n".encode("utf-16"))

    with pytest.raises(ConfigError):
        load_config(path)


def test_file_overrides_defaults(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        "read:\n  max_lines: 25\nsearch:\n  max_results: 7\n  timeout: 2.5\n",
    )

    config = load_config(path)

    assert config.read.max_lines == 25
    assert config.read.max_bytes == DEFAULT_MAX_BYTES
    assert config.search.max_results == 7
    assert config.search.timeout == 2.5


def test_cli_overrides_beat_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "read:\n  max_lines: 25\nsearch:\n  max_results: 7\n")

    config = load_config(path, {"read": {"max_lines": 3}})

    assert config.read.max_lines == 3
    assert config.search.max_results == 7


def test_cli_overrides_work_without_a_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")

    config = load_config(overrides={"search": {"timeout": 1.0}})

    assert config.search.timeout == 1.0
    assert config.search.max_results == DEFAULT_MAX_RESULTS


def test_partial_override_keeps_sibling_values(tmp_path: Path) -> None:
    path = write_config(tmp_path, "read:\n  max_lines: 25\n  max_bytes: 512\n")

    config = load_config(path, {"read": {"max_bytes": 64}})

    assert config.read.max_lines == 25
    assert config.read.max_bytes == 64


def test_unset_cli_values_do_not_override_the_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, "search:\n  max_results: 7\n")

    config = load_config(path, {"search": {"max_results": None, "timeout": None}})

    assert config.search.max_results == 7
    assert config.search.timeout == DEFAULT_TIMEOUT


def test_empty_file_uses_defaults(tmp_path: Path) -> None:
    path = write_config(tmp_path, "")

    config = load_config(path)

    assert config.search.max_results == DEFAULT_MAX_RESULTS


def test_explicit_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(tmp_path / "typo.yaml")


@pytest.mark.parametrize(
    "text",
    [
        "read: [1, 2]\n",
        "read: 5\n",
        "read:\n  max_lines: 0\n",
        "read:\n  max_bytes: nope\n",
        "search:\n  max_results: 0\n",
        "search:\n  max_results: 501\n",
        "search:\n  timeout: 0\n",
        "search:\n  max_result: 5\n",
        "unknown_key: 1\n",
        "security:\n  unknown_key: 1\n",
        "security:\n  allowed_roots: /root/a\n",
    ],
)
def test_invalid_config_is_rejected(tmp_path: Path, text: str) -> None:
    path = write_config(tmp_path, text)

    with pytest.raises(ConfigError):
        load_config(path)


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "read: [\n")

    with pytest.raises(ConfigError):
        load_config(path)


def test_non_mapping_config_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, "- read\n- search\n")

    with pytest.raises(ConfigError):
        load_config(path)

def test_cli_override_shape_matches_what_main_builds(tmp_path: Path) -> None:
    """main.py passes every flag, including the unset ones, in this exact shape."""

    path = write_config(tmp_path, "read:\n  max_lines: 25\nsearch:\n  timeout: 9.0\n")

    overrides = {
        "read": {"max_lines": None, "max_bytes": 128},
        "search": {"max_results": None, "timeout": None},
    }

    config = load_config(path, overrides)

    assert config.read.max_lines == 25
    assert config.read.max_bytes == 128
    assert config.search.max_results == DEFAULT_MAX_RESULTS
    assert config.search.timeout == 9.0


def test_all_cli_overrides_land_when_every_flag_is_given(tmp_path: Path) -> None:
    path = write_config(tmp_path, "read:\n  max_lines: 25\nsearch:\n  max_results: 7\n")

    overrides = {
        "read": {"max_lines": 2, "max_bytes": 256},
        "search": {"max_results": 3, "timeout": 1.5},
    }

    config = load_config(path, overrides)

    assert (config.read.max_lines, config.read.max_bytes) == (2, 256)
    assert (config.search.max_results, config.search.timeout) == (3, 1.5)


def test_cli_max_results_above_the_hard_cap_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")

    with pytest.raises(ConfigError):
        load_config(overrides={"search": {"max_results": 501}})


def test_cli_max_lines_below_one_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")

    with pytest.raises(ConfigError):
        load_config(overrides={"read": {"max_lines": 0}})


def test_cli_timeout_must_be_positive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml")

    with pytest.raises(ConfigError):
        load_config(overrides={"search": {"timeout": 0}})


def test_workspace_is_not_a_config_field(tmp_path: Path) -> None:
    """The default workspace comes from optional --workspace or cwd, never from config.yaml."""

    path = write_config(tmp_path, "workspace: /somewhere\n")

    with pytest.raises(ConfigError):
        load_config(path)


def test_security_defaults_to_no_explicit_allowed_roots() -> None:
    assert SecurityConfig().allowed_roots == []
    assert AppConfig().security == SecurityConfig()


def test_security_allowed_roots_load_from_yaml(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, "security:\n  allowed_roots:\n    - /root/a\n    - /root/b\n"
    )

    assert load_config(path).security.allowed_roots == ["/root/a", "/root/b"]


def test_windows_allowed_roots_use_yaml_single_quotes(tmp_path: Path) -> None:
    path = write_config(
        tmp_path, "security:\n  allowed_roots:\n    - 'D:\\Projects'\n    - 'E:\\Work'\n"
    )

    assert load_config(path).security.allowed_roots == [r"D:\Projects", r"E:\Work"]


def test_agent_defaults_are_optional() -> None:
    assert AppConfig().agent == AgentConfig()
    assert AgentConfig().model_dump() == {
        "device_id": None, "hub_url": None, "token_file": None, "proxy": "system",
    }


def test_agent_config_loads_from_yaml(tmp_path: Path) -> None:
    path = write_config(tmp_path, """agent:
  device_id: desktop
  hub_url: wss://hub.example.com/device/ws
  token_file: ~/.chat2local/hub.token
""")
    assert load_config(path).agent == AgentConfig(
        device_id="desktop", hub_url="wss://hub.example.com/device/ws",
        token_file="~/.chat2local/hub.token",
    )


@pytest.mark.parametrize("field", ["device_id", "hub_url", "token_file"])
@pytest.mark.parametrize("value", ["null", "123", "true"])
def test_agent_config_optional_strings(tmp_path: Path, field, value) -> None:
    path = write_config(tmp_path, f"agent:\n  {field}: {value}\n")
    if value == "null":
        assert getattr(load_config(path).agent, field) is None
    else:
        with pytest.raises(ConfigError):
            load_config(path)


@pytest.mark.parametrize("key", ["device", "hub_urL", "token_path", "token"])
def test_agent_config_rejects_unknown_fields(tmp_path: Path, key) -> None:
    path = write_config(tmp_path, f"agent:\n  {key}: typo\n")
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        load_config(path)


def test_hub_defaults_are_optional() -> None:
    assert AppConfig().hub == HubConfig()
    assert HubConfig().model_dump() == {
        "device_id": None, "host": None, "port": None, "token_file": None,
    }


def test_hub_config_loads_from_yaml(tmp_path: Path) -> None:
    path = write_config(tmp_path, """hub:
  device_id: hub
  host: 127.0.0.1
  port: 8765
  token_file: ~/.chat2local/hub.token
""")
    assert load_config(path).hub == HubConfig(
        device_id="hub", host="127.0.0.1", port=8765,
        token_file="~/.chat2local/hub.token",
    )


@pytest.mark.parametrize("field", ["device_id", "host", "port", "token_file"])
def test_hub_config_fields_accept_null(tmp_path: Path, field) -> None:
    path = write_config(tmp_path, f"hub:\n  {field}: null\n")
    assert getattr(load_config(path).hub, field) is None


@pytest.mark.parametrize("key", ["device", "hostname", "prt", "token_path", "token", "workspace"])
def test_hub_config_rejects_unknown_fields(tmp_path: Path, key) -> None:
    path = write_config(tmp_path, f"hub:\n  {key}: typo\n")
    with pytest.raises(ConfigError, match="Extra inputs are not permitted"):
        load_config(path)


@pytest.mark.parametrize("field,value", [("device_id", "123"), ("host", "true"),
                                         ("port", "nope"), ("token_file", "123")])
def test_hub_config_rejects_invalid_types(tmp_path: Path, field, value) -> None:
    path = write_config(tmp_path, f"hub:\n  {field}: {value}\n")
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("proxy", ["system", "direct", "http://127.0.0.1:7897",
                                  "https://user:pass@proxy.example.com:8443", "socks5h://localhost:1080",
                                  "http://[::1]:7897", "socks4://host:1080", "socks4a://host:1080",
                                  "socks5://user:pass@host:1080"])
def test_agent_proxy_config(tmp_path, proxy):
    path = write_config(tmp_path, f"agent:\n  proxy: '{proxy}'\n")
    assert load_config(path).agent.proxy == proxy


@pytest.mark.parametrize("proxy", [None, True, 1, "", "SYSTEM", "ftp://user:secret@host",
                                  "http://", "http://host:not-a-port", "http://host:65536",
                                  "http://host/path", "http://host?secret=x", "http://host#fragment",
                                  "http://user@host", "http://host\n", "http://host:0",
                                  "http://host/", "http://host:99999", "http://[broken", "http://host\x7f"])
def test_invalid_proxy_rejected_without_echoing_value(proxy):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentConfig(proxy=proxy)


def test_config_error_does_not_echo_credentials(tmp_path):
    path = write_config(tmp_path, "agent:\n  proxy: ftp://user:PRIVATE-PASSWORD@host\n")
    with pytest.raises(ConfigError) as failure:
        load_config(path)
    assert "agent.proxy" in str(failure.value)
    assert "PRIVATE-PASSWORD" not in str(failure.value)
    path.write_text("agent: [http://user:PRIVATE-PASSWORD@host\n", encoding="utf-8")
    with pytest.raises(ConfigError) as failure:
        load_config(path)
    assert "Invalid YAML" in str(failure.value)
    assert "PRIVATE-PASSWORD" not in str(failure.value)
