"""Management CLI wiring without binding ports or touching a running Core."""

from importlib import import_module
from pathlib import Path
import sys

import pytest

from chat2local.runtime import config

main = import_module("chat2local.main")


@pytest.fixture
def launch(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text("agent:\n  proxy: direct\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", path)
    monkeypatch.setattr(main, "configure_logging", lambda **kwargs: None)
    monkeypatch.chdir(tmp_path)
    captured = {}
    async def server(application, **kwargs):
        captured.update(kwargs)
    async def agent(client, **kwargs):
        captured.update(kwargs, client=client)
    monkeypatch.setattr(main, "_run_server", server)
    monkeypatch.setattr(main, "_run_agent", agent)
    def invoke(*args):
        monkeypatch.setattr(sys, "argv", ["chat2local", *args])
        main.main()
        return captured
    return invoke, path


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_all_modes_default_and_cli_management_port_before_or_after_mode(launch, mode):
    invoke, _ = launch
    flags = [] if mode == "standalone" else [mode, "--token", "PRIVATE-CLI-TOKEN"]
    if mode == "agent":
        flags += ["--hub-url", "ws://localhost/device/ws"]
    assert invoke(*flags)["management"].port == 8767
    result = invoke("--management-port", "19766", *flags, "--web-ui")
    assert result["management"].port == 19766 and result["management"].enabled
    assert "PRIVATE-CLI-TOKEN" not in repr(result["command"])
    if mode != "standalone":
        with pytest.raises(Exception, match="token_file"):
            result["command"].checked()
    assert not invoke(*flags, "--no-web-ui")["management"].enabled


def test_browser_and_management_use_separate_fixed_defaults(launch):
    invoke, path = launch
    path.write_text('browser:\n  enabled: true\n  extension_id: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n  token_file: token\n')
    assert invoke()["management"].port == 8767
    with pytest.raises(SystemExit):
        invoke("--management-port", "8766")
    assert invoke("--management-port", "19766")["management"].port == 19766


def test_existing_mcp_port_also_keeps_its_network_behavior(launch):
    invoke, _ = launch
    result = invoke("--port", "8766")
    assert result["port"] == 8766 and result["management"].port == 8767


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_shared_port_rules_respect_mode_config_and_cli(launch, mode):
    invoke, path = launch
    flags = [] if mode == "standalone" else [mode, "--token", "PRIVATE-CLI-TOKEN"]
    if mode == "agent":
        flags += ["--hub-url", "ws://localhost/device/ws"]
    path.write_text("hub:\n  port: 8766\n")
    assert invoke(*flags)["management"].port == 8767
    path.write_text("hub:\n  port: 8767\n")
    assert invoke(*flags)["management"].port == 8767
    assert config.management_ports(config.load_config(path), mode=mode)[0] == (8768 if mode == "hub" else 8767)
    path.write_text("management:\n  port: 8765\n")
    if mode == "agent":
        assert invoke(*flags)["management"].port == 8765  # Agent has no MCP listener.
    else:
        with pytest.raises(SystemExit):
            invoke(*flags)
        result = invoke("--port", "19765", *flags)
        assert result["management"].port == 8765
        assert result["cli_port"] == 19765
    path.write_text("hub:\n  port: 19765\nmanagement:\n  port: 19765\n")
    if mode == "hub":
        with pytest.raises(SystemExit):
            invoke(*flags)
        assert invoke(*flags, "--port", "19764")["management"].port == 19765
    else:
        assert invoke(*flags)["management"].port == 19765


@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_default_management_has_bounded_candidates_but_explicit_ports_are_strict(launch, mode):
    invoke, path = launch
    flags = [] if mode == "standalone" else [mode, "--token", "PRIVATE-CLI-TOKEN"]
    if mode == "agent":
        flags += ["--hub-url", "ws://localhost/device/ws"]
    path.write_text('browser:\n  enabled: true\n  port: 8767\n  extension_id: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n  token_file: token\n')
    assert invoke(*flags)["management"].port == 8767
    assert config.management_ports(config.load_config(path), mode=mode) == tuple(range(8768, 8777))
    with pytest.raises(SystemExit):
        invoke(*flags, "--management-port", "8767")
    assert "management" not in config.read_persisted_config(path)
    assert invoke(*flags, "--management-port", "19766")["management"].port == 19766
    path.write_text("{}")
    if mode == "agent":
        assert invoke("--port", "8767", *flags)["management"].port == 8767
    else:
        assert invoke("--port", "8767", *flags)["management"].port == 8767
        assert config.management_ports(config.load_config(path), mode=mode, cli_port=8767)[0] == 8768
        with pytest.raises(SystemExit):
            invoke("--port", "8767", *flags, "--management-port", "8767")
        assert invoke("--port", "8767", *flags, "--management-port", "19766")["management"].port == 19766


def test_both_core_listeners_are_excluded_and_explicit_default_is_not_a_range():
    settings = config.validate_persisted_config({"browser": {
        "enabled": True, "port": 8768, "extension_id": "a" * 32, "token_file": "browser.token",
    }})
    assert config.management_ports(settings, mode="hub", cli_port=8767) == tuple(range(8769, 8777))
    explicit = config.validate_persisted_config({"management": {"port": 8767}})
    assert config.management_ports(explicit, mode="standalone") == (8767,)


def test_frozen_core_defaults_to_desktop_management(launch, monkeypatch):
    invoke, _ = launch
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert not invoke()["management"].enabled
    assert invoke("--web-ui")["management"].enabled


def test_startup_replay_has_absolute_config_workspace_and_validated_cwd(launch, tmp_path):
    invoke, path = launch
    result = invoke("--config", path.name, "--workspace", str(tmp_path), "--read-max-lines", "11")
    args = result["command"].checked()
    assert args[:3] == (sys.executable, "-m", "chat2local.main")
    assert args[args.index("--config") + 1] == str(path)
    assert args[args.index("--workspace") + 1] == str(tmp_path)
    assert args[args.index("--working-directory") + 1] == str(tmp_path)
    assert "--read-max-lines" in args
    child = tmp_path / "child"
    child.mkdir()
    invoke("--working-directory", str(child))
    assert Path.cwd() == child


def test_startup_cannot_set_cwd_outside_allowed_roots(launch, tmp_path):
    invoke, path = launch
    child = tmp_path / "allowed"
    child.mkdir()
    path.write_text(f"security:\n  allowed_roots: ['{child}']\n")
    with pytest.raises(SystemExit):
        invoke("--workspace", str(child), "--working-directory", str(tmp_path))


def test_env_token_is_not_stored_and_matching_token_file_can_be_used(launch, monkeypatch, tmp_path):
    invoke, path = launch
    token_path = tmp_path / "hub.token"
    token_path.write_text("PRIVATE-ENV-TOKEN")
    monkeypatch.setenv("CHAT2LOCAL_HUB_TOKEN", "PRIVATE-ENV-TOKEN")
    path.write_text(f"hub:\n  token_file: '{token_path}'\n")
    result = invoke("hub")
    assert "PRIVATE-ENV-TOKEN" not in repr(result["command"])
    assert "hub" in result["command"].checked()
