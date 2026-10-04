"""Startup wiring: what ``main()`` binds before Uvicorn serves anything.

The server itself is never started here; ``uvicorn.run`` is replaced so the
test can inspect the runtime state the process would have launched with.
"""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

import pytest

from chat2local.mcp import tools as mcp_tools
from chat2local.runtime.config import (
    DEFAULT_MAX_LINES,
    DEFAULT_MAX_RESULTS,
    DEFAULT_TIMEOUT,
    ReadConfig,
    SearchConfig,
)

# `chat2local/__init__.py` defines its own main(), which shadows the submodule
# attribute, so the module is imported by name rather than via `from ... import`.
main_module = importlib.import_module("chat2local.main")

NL = chr(10)


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run main() with Uvicorn stubbed out and the runtime state restored after."""

    calls: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        calls["app"] = app
        calls.update(kwargs)

    monkeypatch.setattr(main_module.uvicorn, "run", fake_run)

    yield calls


@pytest.fixture(autouse=True)
def restore_runtime(monkeypatch: pytest.MonkeyPatch):
    """Each test starts from an unbound runtime and leaves it that way."""

    monkeypatch.setattr(mcp_tools, "_workspace", None)
    monkeypatch.setattr(mcp_tools, "_config", mcp_tools.AppConfig())
    monkeypatch.setattr(
        "chat2local.runtime.config.DEFAULT_CONFIG_PATH", Path("__missing_test_config__.yaml")
    )

    yield


def write_config(directory: Path, text: str) -> Path:
    path = directory / "config.yaml"

    with path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)

    return path


def run_main(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr("sys.argv", ["chat2local", *argv])

    main_module.main()


# --- in-process startup --------------------------------------------------------


def test_startup_binds_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.chdir(tmp_path)

    run_main(monkeypatch, "--workspace", str(root))

    workspace = mcp_tools.get_workspace()

    assert workspace.root == root.resolve()
    assert workspace.allowed_roots == (root.resolve(),)
    assert launched["app"] == "chat2local.app:app"
    assert (launched["host"], launched["port"], launched["reload"]) == (
        "127.0.0.1",
        8765,
        False,
    )


def test_startup_binds_the_app_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config_path = write_config(tmp_path, "read:" + NL + "  max_lines: 7" + NL)

    run_main(monkeypatch, "--workspace", str(root), "--config", str(config_path))

    assert mcp_tools.get_config().read.max_lines == 7


def test_startup_uses_defaults_without_a_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        "chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml"
    )
    root = tmp_path / "project"
    root.mkdir()

    run_main(monkeypatch, "--workspace", str(root))

    config = mcp_tools.get_config()

    assert config.read == ReadConfig()
    assert config.search == SearchConfig()


def test_startup_cli_overrides_beat_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config_path = write_config(
        tmp_path,
        "read:" + NL + "  max_lines: 7" + NL + "search:" + NL + "  max_results: 9" + NL,
    )

    run_main(
        monkeypatch,
        "--workspace",
        str(root),
        "--config",
        str(config_path),
        "--read-max-lines",
        "3",
        "--search-max-results",
        "4",
    )

    config = mcp_tools.get_config()

    assert config.read.max_lines == 3
    assert config.search.max_results == 4


def test_startup_cli_overrides_fill_in_for_unset_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    monkeypatch.setattr(
        "chat2local.runtime.config.DEFAULT_CONFIG_PATH", tmp_path / "absent.yaml"
    )
    root = tmp_path / "project"
    root.mkdir()

    run_main(monkeypatch, "--workspace", str(root), "--search-timeout", "2.5")

    config = mcp_tools.get_config()

    assert config.search.timeout == 2.5
    assert config.read.max_lines == DEFAULT_MAX_LINES
    assert config.search.max_results == DEFAULT_MAX_RESULTS


def test_startup_leaves_the_runtime_untouched_when_the_config_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    """A typo in --config must stop startup, not bind a half-configured runtime."""

    root = tmp_path / "project"
    root.mkdir()

    with pytest.raises(SystemExit):
        run_main(
            monkeypatch,
            "--workspace",
            str(root),
            "--config",
            str(tmp_path / "typo.yaml"),
        )

    assert launched == {}
    assert mcp_tools._workspace is None


def test_startup_rejects_an_invalid_config_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config_path = write_config(tmp_path, "search:" + NL + "  max_results: 501" + NL)

    with pytest.raises(SystemExit):
        run_main(monkeypatch, "--workspace", str(root), "--config", str(config_path))

    assert launched == {}


def test_startup_rejects_a_missing_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "--workspace", str(tmp_path / "nope"))

    assert launched == {}


@pytest.mark.parametrize("security_yaml", [None, "security:\n  allowed_roots: []\n"])
def test_startup_uses_cwd_when_workspace_is_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any],
    security_yaml: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    argv = ()
    if security_yaml is not None:
        config_path = write_config(tmp_path, security_yaml)
        argv = ("--config", str(config_path))

    run_main(monkeypatch, *argv)

    workspace = mcp_tools.get_workspace()
    assert workspace.root == tmp_path.resolve()
    assert workspace.allowed_roots == (tmp_path.resolve(),)
    assert launched["app"] == "chat2local.app:app"


# --- parser --------------------------------------------------------------------


def test_config_flag_defaults_to_none() -> None:
    args = main_module.build_parser().parse_args(["--workspace", "."])

    assert args.config is None


def test_all_override_flags_default_to_none() -> None:
    args = main_module.build_parser().parse_args(["--workspace", "."])

    assert args.read_max_lines is None
    assert args.read_max_bytes is None
    assert args.search_max_results is None
    assert args.search_timeout is None


def test_workspace_is_optional() -> None:
    parser = main_module.build_parser()

    assert parser.parse_args([]).workspace is None

    args = parser.parse_args(["--workspace", "somewhere"])

    assert args.workspace == "somewhere"
    assert args.search_max_results is None


def test_default_timeout_constant_is_ten_seconds() -> None:
    assert DEFAULT_TIMEOUT == 10.0


@pytest.mark.parametrize("security_yaml", ["", "security:\n  allowed_roots: []\n"])
def test_startup_falls_back_to_workspace_when_allowed_roots_are_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any],
    security_yaml: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config_path = write_config(tmp_path, security_yaml)
    real_configure = main_module.configure_workspace
    captured: dict[str, Any] = {}

    def configure(path, *, allowed_roots):
        captured["allowed_roots"] = allowed_roots
        return real_configure(path, allowed_roots=allowed_roots)

    monkeypatch.setattr(main_module, "configure_workspace", configure)
    run_main(monkeypatch, "--workspace", str(root), "--config", str(config_path))

    assert captured["allowed_roots"] == [root]
    assert mcp_tools.get_workspace().allowed_roots == (root.resolve(),)
    assert mcp_tools.get_config().security.allowed_roots == []
    assert launched["app"] == "chat2local.app:app"


def test_startup_accepts_cwd_inside_configured_allowed_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    config_path = write_config(
        tmp_path, f"security:\n  allowed_roots:\n    - '{tmp_path}'\n"
    )

    monkeypatch.chdir(root)
    run_main(monkeypatch, "--config", str(config_path))

    assert mcp_tools.get_workspace().root == root.resolve()
    assert mcp_tools.get_workspace().allowed_roots == (tmp_path.resolve(),)
    assert launched["app"] == "chat2local.app:app"


def test_startup_rejects_workspace_outside_configured_allowed_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any], capsys
) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    config_path = write_config(
        tmp_path, f"security:\n  allowed_roots:\n    - '{allowed}'\n"
    )

    with pytest.raises(SystemExit) as failure:
        run_main(monkeypatch, "--workspace", str(outside), "--config", str(config_path))

    assert failure.value.code == 2
    assert "Workspace is outside allowed roots" in capsys.readouterr().err
    assert launched == {}
    assert mcp_tools._workspace is None
    assert mcp_tools.get_config() == mcp_tools.AppConfig()


@pytest.mark.parametrize("explicit_workspace", [False, True])
def test_startup_with_cwd_outside_configured_allowed_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: dict[str, Any],
    capsys, explicit_workspace: bool,
) -> None:
    allowed = tmp_path / "allowed"
    project = allowed / "project"
    project.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    config_path = write_config(
        tmp_path, f"security:\n  allowed_roots:\n    - '{allowed}'\n"
    )
    monkeypatch.chdir(outside)

    if explicit_workspace:
        run_main(monkeypatch, "--config", str(config_path), "--workspace", str(project))
        workspace = mcp_tools.get_workspace()
        assert workspace.root == project.resolve()
        assert workspace.allowed_roots == (allowed.resolve(),)
        assert launched["app"] == "chat2local.app:app"
    else:
        with pytest.raises(SystemExit) as failure:
            run_main(monkeypatch, "--config", str(config_path))
        assert failure.value.code == 2
        assert "Workspace is outside allowed roots" in capsys.readouterr().err
        assert launched == {}
        assert mcp_tools._workspace is None
        assert mcp_tools.get_config() == mcp_tools.AppConfig()


@pytest.mark.parametrize("mode", ["hub", "agent"])
def test_network_modes_require_token(monkeypatch, launched, capsys, mode):
    monkeypatch.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
    argv = [mode]
    if mode == "agent":
        argv += ["--hub-url", "ws://localhost:8765/device/ws"]
    with pytest.raises(SystemExit) as failure:
        run_main(monkeypatch, *argv)
    assert failure.value.code == 2
    assert "Hub token is required" in capsys.readouterr().err
    assert launched == {}
    assert mcp_tools._workspace is None


@pytest.mark.parametrize("mode", ["hub", "agent"])
@pytest.mark.parametrize("cli_token", [None, "cli-token"])
def test_network_mode_token_priority_and_startup(tmp_path, monkeypatch, launched, mode, cli_token):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHAT2LOCAL_HUB_TOKEN", "environment-token")
    captured = {}
    argv = [mode, "--device-id", "my-device"]
    if cli_token is not None:
        argv += ["--token", cli_token]
    expected_token = cli_token or "environment-token"
    if mode == "agent":
        from chat2local.agent.client import AgentClient
        argv += ["--hub-url", "ws://localhost:8765/device/ws"]

        def unused_router(*args, **kwargs):
            raise AssertionError("Agent startup must not create a DeviceRouter")

        monkeypatch.setattr(main_module, "DeviceRouter", unused_router)

        async def fake_agent_run(client):
            captured["token"] = client.token
            captured["device"] = client.device_id
            captured["workspace"] = client.local.workspace

        monkeypatch.setattr(AgentClient, "run", fake_agent_run)
    else:
        from chat2local import app as app_module
        real = app_module.create_app

        def create(router, *, hub_token):
            captured["token"] = hub_token
            return real(router, hub_token=hub_token)

        monkeypatch.setattr(app_module, "create_app", create)
        argv += ["--host", "0.0.0.0", "--port", "9123"]

    run_main(monkeypatch, *argv)
    assert captured["token"] == expected_token
    assert mcp_tools.get_workspace().root == tmp_path.resolve()
    assert mcp_tools.get_workspace().allowed_roots == (tmp_path.resolve(),)
    if mode == "agent":
        assert launched == {}
        assert captured["device"] == "my-device"
        assert captured["workspace"].root == tmp_path.resolve()
    else:
        router = launched["app"].state.router
        assert router.device_id == "my-device"
        assert router.registry.sessions == {}
        assert launched["host"] == "0.0.0.0" and launched["port"] == 9123
        assert any(getattr(route, "path", None) == "/device/ws" for route in launched["app"].routes)


def test_standalone_device_id_and_no_token(tmp_path, monkeypatch, launched):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
    run_main(monkeypatch, "--device-id", "desktop")
    assert mcp_tools.get_router().device_id == "desktop"
    assert mcp_tools.get_router().registry is None
    assert launched["app"] == "chat2local.app:app"


@pytest.mark.parametrize("mode", ["hub", "agent"])
def test_network_modes_enforce_local_allowed_roots(tmp_path, monkeypatch, launched, mode):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    path = write_config(tmp_path, f"security:\n  allowed_roots:\n    - '{allowed}'\n")
    monkeypatch.chdir(outside)
    argv = [mode, "--config", str(path), "--token", "test-token"]
    if mode == "agent":
        argv += ["--hub-url", "ws://localhost:8765/device/ws"]
    with pytest.raises(SystemExit):
        run_main(monkeypatch, *argv)
    assert launched == {}
    assert mcp_tools._workspace is None


@pytest.mark.parametrize("device", ["", "two devices"])
@pytest.mark.parametrize("mode", ["standalone", "hub", "agent"])
def test_invalid_device_identity_rejected(tmp_path, monkeypatch, launched, device, mode):
    monkeypatch.chdir(tmp_path)
    argv = [] if mode == "standalone" else [mode, "--token", "test-token"]
    if mode == "agent":
        argv += ["--hub-url", "ws://localhost:8765/device/ws"]
    with pytest.raises(SystemExit):
        run_main(monkeypatch, *argv, "--device-id", device)
    assert launched == {}


@pytest.mark.parametrize("url", ["https://hub/device/ws", "ws://", "ws://user:pass@hub/ws"])
def test_agent_rejects_bad_hub_url(tmp_path, monkeypatch, launched, url):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        run_main(monkeypatch, "agent", "--hub-url", url, "--token", "test-token")
    assert launched == {}


def test_common_flags_before_subcommand_are_preserved():
    args = main_module.build_parser().parse_args([
        "--workspace", "project", "--device-id", "server", "--port", "9123", "hub", "--token", "test-token",
    ])
    assert (args.workspace, args.device_id, args.port) == ("project", "server", 9123)
    args = main_module.build_parser().parse_args(["--device-id", "original", "hub", "--device-id", "override"])
    assert args.device_id == "override"


def test_startup_reports_unavailable_shell(tmp_path, monkeypatch, launched, capsys):
    from chat2local.runtime.shell import ShellError, ShellResolver
    monkeypatch.chdir(tmp_path)
    def unavailable(self):
        raise ShellError("Shell is unavailable: configured-shell")
    monkeypatch.setattr(ShellResolver, "resolve", unavailable)
    with pytest.raises(SystemExit) as failure:
        run_main(monkeypatch)
    assert failure.value.code == 2 and "Shell is unavailable" in capsys.readouterr().err
    assert launched == {}


@pytest.fixture
def agent_started(tmp_path, monkeypatch, launched):
    from chat2local.agent.client import AgentClient

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
    captured = {}

    async def run(client):
        captured.update(device_id=client.device_id, hub_url=client.hub_url,
                        token=client.token, workspace=client.local.workspace.root)

    monkeypatch.setattr(AgentClient, "run", run)
    return captured


def test_agent_starts_from_default_config_only(tmp_path, monkeypatch, launched, agent_started):
    token_file = tmp_path / "hub.token"
    token_file.write_text(" \t配置令牌\r\n", encoding="utf-8")
    path = write_config(tmp_path, f"""agent:
  device_id: desktop
  hub_url: wss://hub.example.com/device/ws
  token_file: '{token_file}'
""")
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", path)

    run_main(monkeypatch, "agent")

    assert agent_started == {
        "device_id": "desktop", "hub_url": "wss://hub.example.com/device/ws",
        "token": "配置令牌", "workspace": tmp_path.resolve(),
    }
    assert launched == {}


@pytest.mark.parametrize("before_subcommand", [False, True])
def test_agent_cli_overrides_config(tmp_path, monkeypatch, agent_started, before_subcommand):
    path = write_config(tmp_path, """agent:
  device_id: configured
  hub_url: wss://configured/device/ws
  token_file: missing.token
""")
    monkeypatch.setenv("CHAT2LOCAL_HUB_TOKEN", "env-token")
    common = ["--device-id", "cli-device", "--config", str(path)]
    argv = [*common, "agent"] if before_subcommand else ["agent", *common]
    run_main(monkeypatch, *argv, "--hub-url", "ws://cli/device/ws", "--token", "cli-token")

    assert agent_started["device_id"] == "cli-device"
    assert agent_started["hub_url"] == "ws://cli/device/ws"
    assert agent_started["token"] == "cli-token"


@pytest.mark.parametrize("file_exists", [False, True])
def test_agent_environment_token_overrides_file(tmp_path, monkeypatch, agent_started, file_exists):
    token_file = tmp_path / "hub.token"
    if file_exists:
        token_file.write_text("file-token\n", encoding="utf-8")
    path = write_config(tmp_path, f"agent:\n  hub_url: ws://localhost/device/ws\n  token_file: '{token_file}'\n")
    monkeypatch.setenv("CHAT2LOCAL_HUB_TOKEN", "environment-token")
    monkeypatch.setattr(main_module.socket, "gethostname", lambda: "hostname-device")

    run_main(monkeypatch, "agent", "--config", str(path))

    assert agent_started["token"] == "environment-token"
    assert agent_started["device_id"] == "hostname-device"


@pytest.mark.parametrize("mode", ["hub", "agent"])
def test_network_mode_expands_token_file_home(tmp_path, monkeypatch, launched, agent_started, mode):
    fields = "  hub_url: ws://localhost/device/ws\n" if mode == "agent" else ""
    path = write_config(tmp_path, f"{mode}:\n{fields}  token_file: ~/.chat2local/hub.token\n")
    expected = Path.home() / ".chat2local" / "hub.token"
    real_read = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        if path == expected:
            reads.append((path, kwargs))
            return " token-from-home\n"
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    run_main(monkeypatch, mode, "--config", str(path))

    if mode == "agent":
        assert agent_started["token"] == "token-from-home"
    else:
        assert launched["app"].state.router.registry is not None
    assert reads == [(expected, {"encoding": "utf-8"})]


@pytest.mark.parametrize("contents", [None, "", " \t\r\n"])
@pytest.mark.parametrize("mode", ["hub", "agent"])
def test_network_mode_missing_or_empty_token_file(tmp_path, monkeypatch, launched, agent_started, capsys, contents, mode):
    token_file = tmp_path / "hub.token"
    if contents is not None:
        token_file.write_text(contents, encoding="utf-8")
    fields = "  hub_url: ws://localhost/device/ws\n" if mode == "agent" else ""
    path = write_config(tmp_path, f"{mode}:\n{fields}  token_file: '{token_file}'\n")

    with pytest.raises(SystemExit) as failure:
        run_main(monkeypatch, mode, "--config", str(path))

    assert failure.value.code == 2
    expected = "does not exist" if contents is None else "is empty"
    assert f"{mode.capitalize()} token file {expected}" in capsys.readouterr().err
    assert agent_started == {} and launched == {}
    assert mcp_tools._workspace is None


@pytest.mark.parametrize("failure_kind", ["permission", "invalid_utf8"])
@pytest.mark.parametrize("mode", ["hub", "agent"])
def test_network_mode_unreadable_token_file_does_not_echo_contents(
    tmp_path, monkeypatch, launched, agent_started, capsys, failure_kind, mode,
):
    token_file = tmp_path / "hub.token"
    sensitive = b"secret-token\xff"
    token_file.write_bytes(sensitive)
    fields = "  hub_url: ws://localhost/device/ws\n" if mode == "agent" else ""
    path = write_config(tmp_path, f"{mode}:\n{fields}  token_file: '{token_file}'\n")
    if failure_kind == "permission":
        real_read = Path.read_text

        def read(path, *args, **kwargs):
            if path == token_file:
                raise PermissionError("secret-token")
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)

    with pytest.raises(SystemExit) as failure:
        run_main(monkeypatch, mode, "--config", str(path))

    assert failure.value.code == 2
    error = capsys.readouterr().err
    assert f"Could not read {mode.capitalize()} token file as UTF-8 text" in error
    assert "secret-token" not in error
    assert agent_started == {} and launched == {} and mcp_tools._workspace is None


def test_agent_requires_hub_url(tmp_path, monkeypatch, launched, agent_started, capsys):
    with pytest.raises(SystemExit) as failure:
        run_main(monkeypatch, "agent", "--token", "test-token")
    assert failure.value.code == 2
    assert "use --hub-url or agent.hub_url in config.yaml" in capsys.readouterr().err
    assert agent_started == {} and launched == {}
    assert mcp_tools._workspace is None


@pytest.mark.parametrize("mode", ["hub", "standalone"])
def test_non_agent_modes_ignore_agent_settings(tmp_path, monkeypatch, launched, mode):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
    monkeypatch.setattr(main_module.socket, "gethostname", lambda: "local-hostname")
    path = write_config(tmp_path, """agent:
  device_id: configured-agent
  hub_url: wss://configured/device/ws
  token_file: missing.token
""")
    argv = ["hub", "--token", "hub-token"] if mode == "hub" else []
    run_main(monkeypatch, *argv, "--config", str(path))
    assert mcp_tools.get_router().device_id == "local-hostname"
    if mode == "hub":
        assert launched["app"].state.router.registry is not None
    else:
        assert launched["app"] == "chat2local.app:app"


def test_hub_does_not_use_agent_token_file(tmp_path, monkeypatch, launched, capsys):
    monkeypatch.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
    token_file = tmp_path / "hub.token"
    token_file.write_text("agent-only-token", encoding="utf-8")
    path = write_config(tmp_path, f"agent:\n  token_file: '{token_file}'\n")
    with pytest.raises(SystemExit) as failure:
        run_main(monkeypatch, "hub", "--config", str(path))
    assert failure.value.code == 2
    error = capsys.readouterr().err
    assert "Hub token is required" in error and "hub.token_file" in error
    assert launched == {} and mcp_tools._workspace is None


@pytest.fixture
def hub_started(tmp_path, monkeypatch, launched):
    from chat2local import app as app_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
    captured = {}
    real_create = app_module.create_app

    def create(router, *, hub_token):
        captured.update(token=hub_token, device_id=router.device_id,
                        workspace=router.local.workspace.root)
        return real_create(router, hub_token=hub_token)

    monkeypatch.setattr(app_module, "create_app", create)
    return captured


def test_hub_starts_from_default_config_only(tmp_path, monkeypatch, launched, hub_started):
    token_file = tmp_path / "hub.token"
    token_file.write_text(" \t共享令牌\r\n", encoding="utf-8")
    path = write_config(tmp_path, f"""hub:
  device_id: hub
  host: 0.0.0.0
  port: 9123
  token_file: '{token_file}'
agent:
  device_id: configured-agent
  token_file: missing-agent.token
""")
    monkeypatch.setattr("chat2local.runtime.config.DEFAULT_CONFIG_PATH", path)

    run_main(monkeypatch, "hub")

    assert hub_started == {
        "device_id": "hub", "token": "共享令牌", "workspace": tmp_path.resolve(),
    }
    assert launched["host"] == "0.0.0.0" and launched["port"] == 9123
    assert launched["app"].state.router.registry is not None
    assert any(getattr(route, "path", None) == "/device/ws" for route in launched["app"].routes)


@pytest.mark.parametrize("before_subcommand", [False, True])
def test_hub_cli_overrides_config(tmp_path, monkeypatch, launched, hub_started, before_subcommand):
    path = write_config(tmp_path, """hub:
  device_id: configured-hub
  host: 0.0.0.0
  port: 9123
  token_file: missing.token
""")
    monkeypatch.setenv("CHAT2LOCAL_HUB_TOKEN", "env-token")
    common = ["--device-id", "cli-hub", "--host", "127.0.0.2", "--port", "9876", "--config", str(path)]
    argv = [*common, "hub"] if before_subcommand else ["hub", *common]
    run_main(monkeypatch, *argv, "--token", "cli-token")

    assert hub_started["token"] == "cli-token" and hub_started["device_id"] == "cli-hub"
    assert launched["host"] == "127.0.0.2" and launched["port"] == 9876


@pytest.mark.parametrize("file_exists", [False, True])
def test_hub_environment_token_overrides_file(tmp_path, monkeypatch, launched, hub_started, file_exists):
    token_file = tmp_path / "hub.token"
    if file_exists:
        token_file.write_text("file-token\n", encoding="utf-8")
    path = write_config(tmp_path, f"hub:\n  token_file: '{token_file}'\n")
    monkeypatch.setenv("CHAT2LOCAL_HUB_TOKEN", "environment-token")
    monkeypatch.setattr(main_module.socket, "gethostname", lambda: "hostname-hub")

    run_main(monkeypatch, "hub", "--config", str(path))

    assert hub_started["token"] == "environment-token"
    assert hub_started["device_id"] == "hostname-hub"
    assert launched["host"] == "127.0.0.1" and launched["port"] == 8765


@pytest.mark.parametrize("mode", ["hub", "standalone"])
def test_server_parser_leaves_listen_defaults_unset(mode):
    args = main_module.build_parser().parse_args(["hub"] if mode == "hub" else [])
    assert args.device_id is None and args.host is None and args.port is None


def test_standalone_ignores_both_role_configs(tmp_path, monkeypatch, launched):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHAT2LOCAL_HUB_TOKEN", raising=False)
    monkeypatch.setattr(main_module.socket, "gethostname", lambda: "standalone-hostname")
    path = write_config(tmp_path, """hub:
  device_id: configured-hub
  host: 0.0.0.0
  port: 9999
  token_file: missing-hub.token
agent:
  device_id: configured-agent
  hub_url: wss://configured/device/ws
  token_file: missing-agent.token
""")

    def unexpected(*args, **kwargs):
        raise AssertionError("Standalone must not resolve any role token")

    monkeypatch.setattr(main_module, "_resolve_token", unexpected)
    run_main(monkeypatch, "--config", str(path))

    assert mcp_tools.get_router().device_id == "standalone-hostname"
    assert mcp_tools.get_workspace().root == tmp_path.resolve()
    assert launched["app"] == "chat2local.app:app"
    assert launched["host"] == "127.0.0.1" and launched["port"] == 8765


def test_agent_ignores_hub_config(tmp_path, monkeypatch, agent_started):
    path = write_config(tmp_path, """hub:
  device_id: configured-hub
  token_file: missing-hub.token
agent:
  hub_url: ws://localhost/device/ws
""")
    monkeypatch.setattr(main_module.socket, "gethostname", lambda: "agent-hostname")
    run_main(monkeypatch, "agent", "--config", str(path), "--token", "agent-token")
    assert agent_started["device_id"] == "agent-hostname"
    assert agent_started["token"] == "agent-token"
