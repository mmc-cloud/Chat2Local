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
