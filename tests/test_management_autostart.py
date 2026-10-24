"""OS startup adapters use simulated managers, never the machine's services."""

import ast
import json
import plistlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_gui_desktop import FakeRegistry

from chat2local.management import autostart
from chat2local.management.autostart import CoreAutostart, StartupCommand
from chat2local.management.runtime import ManagementError
from chat2local.runtime.config import (
    AppConfig,
    AutostartConfig,
    ConfigError,
    validate_persisted_config,
)
from chat2local.runtime.windows_startup import WindowsRunEntry


@pytest.fixture
def command(tmp_path):
    directory = tmp_path / "工作区 with spaces"
    directory.mkdir()
    python = tmp_path / "venv with spaces/python.exe"
    python.parent.mkdir()
    python.touch()
    return StartupCommand((str(python), "-m", "chat2local.main", "agent",
                           "--workspace", str(directory), "--working-directory", str(directory),
                           "--config", str(tmp_path / "custom config.yaml")), directory)


@pytest.fixture
def windows_command(tmp_path, monkeypatch):
    """Control command lengths while keeping every file operation in tmp_path."""
    root = Path(tmp_path.anchor) / "Chat2Local 测试"
    python = root / "venv with spaces/python.exe"
    directory = root / "工作区 with spaces"
    data = root / "data"
    paths = {
        python: tmp_path / "python.exe",
        python.with_name("pythonw.exe"): tmp_path / "pythonw.exe",
        directory: tmp_path / "workspace",
        data / "windows-core-startup.py": tmp_path / "windows-core-startup.py",
    }
    paths[python].touch()
    paths[directory].mkdir()
    # Only these controlled paths are redirected; pytest and other paths keep
    # their normal filesystem behavior. The real atomic writer and ACLs run.
    for name in (
        "is_file",
        "is_dir",
        "exists",
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
        "unlink",
        "touch",
        "mkdir",
        "rmdir",
    ):
        operation = getattr(Path, name)

        def redirected(path, *args, _operation=operation, **kwargs):
            return _operation(paths.get(path, path), *args, **kwargs)

        monkeypatch.setattr(Path, name, redirected)
    write = autostart._atomic_write

    def write_startup(path, contents, *, private_windows=False):
        assert path == data / "windows-core-startup.py"
        assert private_windows
        write(paths[path], contents, private_windows=private_windows)

    monkeypatch.setattr(autostart, "_atomic_write", write_startup)
    monkeypatch.setattr(autostart.config, "user_data_directory", lambda: data)
    return StartupCommand((str(python), "-m", "chat2local.main"), directory)


@pytest.fixture
def long_windows_command(windows_command):
    command = windows_command
    return StartupCommand(
        (
            *command.args,
            "agent",
            "--workspace",
            str(command.directory),
            "--working-directory",
            str(command.directory),
            "--device-id",
            "x" * 260,
        ),
        command.directory,
    )


def platform(monkeypatch, name):
    monkeypatch.setattr(autostart, "sys", SimpleNamespace(platform=name))


def test_windows_uses_separate_run_entry_and_quoted_direct_core_command(
    monkeypatch, windows_command
):
    command = windows_command
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    registry.values["Chat2Local"] = ("existing desktop startup", registry.REG_SZ)
    original = registry.values.copy()
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    command = StartupCommand(
        (
            command.args[0],
            "-m",
            "chat2local.main",
            "--workspace",
            str(command.directory),
        ),
        command.directory,
    )
    expected = subprocess.list2cmdline(command.args)
    assert len(expected.encode("utf-16-le")) // 2 <= 260
    service = CoreAutostart(command)
    assert service.status()["enabled"] is False
    assert service.set_enabled(True)["enabled"] is True
    assert registry.values["Chat2LocalCore"][0] == expected
    assert f'"{command.args[0]}"' in expected
    assert f'"{command.directory}"' in expected
    assert not (
        autostart.config.user_data_directory() / "windows-core-startup.py"
    ).exists()
    assert "--core" not in registry.values["Chat2LocalCore"][0]
    count = len(registry.writes)
    service.set_enabled(True)
    assert len(registry.writes) == count
    service.set_enabled(False)
    service.set_enabled(False)
    assert registry.values == original


def test_windows_long_command_uses_private_same_process_startup_script(
    monkeypatch, long_windows_command
):
    command = long_windows_command
    assert len(subprocess.list2cmdline(command.args).encode("utf-16-le")) // 2 > 260
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    service = CoreAutostart(command)
    service.set_enabled(True)
    line = registry.values[autostart.CORE_RUN_VALUE][0]
    assert len(line.encode("utf-16-le")) // 2 <= 260
    script = autostart.config.user_data_directory() / "windows-core-startup.py"
    assert line == subprocess.list2cmdline([command.args[0], str(script)])
    tree = ast.parse(script.read_text(encoding="utf-8"))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign))
    assert ast.literal_eval(assignment.value) == ["chat2local", *command.args[3:]]
    assert "subprocess" not in script.read_text(encoding="utf-8")


@pytest.mark.parametrize("previous", [None, b"# previous startup script\r\n"])
def test_windows_long_command_registry_failure_restores_startup_script(
    monkeypatch, long_windows_command, previous
):
    command = long_windows_command
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    service = CoreAutostart(command)
    script = autostart.config.user_data_directory() / "windows-core-startup.py"
    if previous is not None:
        script.write_bytes(previous)
    monkeypatch.setattr(
        registry, "SetValueEx", lambda *args: (_ for _ in ()).throw(PermissionError())
    )
    with pytest.raises(ManagementError):
        service.set_enabled(True)
    assert (script.read_bytes() if script.exists() else None) == previous
    assert autostart.CORE_RUN_VALUE not in registry.values


@pytest.mark.parametrize("units", [260, 261])
def test_windows_run_limit_counts_utf16_units(monkeypatch, windows_command, units):
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    args = (*windows_command.args, "--device-id", "测试😀")
    padding = units - len(subprocess.list2cmdline(args).encode("utf-16-le")) // 2
    command = StartupCommand(
        (*args[:-1], args[-1] + "x" * padding), windows_command.directory
    )
    direct = subprocess.list2cmdline(command.args)
    assert len(direct.encode("utf-16-le")) // 2 == units
    assert CoreAutostart(command).set_enabled(True)["enabled"] is True
    line = registry.values[autostart.CORE_RUN_VALUE][0]
    script = autostart.config.user_data_directory() / "windows-core-startup.py"
    assert script.exists() is (units > 260)
    assert line == (
        direct
        if units == 260
        else subprocess.list2cmdline([command.args[0], str(script)])
    )


def test_windows_rejects_command_when_startup_script_path_is_still_too_long(
    monkeypatch, long_windows_command
):
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    registry.values["Chat2Local"] = ("existing desktop startup", registry.REG_SZ)
    registry.values[autostart.CORE_RUN_VALUE] = (
        "previous core startup",
        registry.REG_SZ,
    )
    original = registry.values.copy()
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    script = autostart.config.user_data_directory() / "windows-core-startup.py"
    directory = script.parent / ("deep" * 80)
    monkeypatch.setattr(autostart.config, "user_data_directory", lambda: directory)
    shortened = subprocess.list2cmdline(
        [long_windows_command.args[0], str(directory / "windows-core-startup.py")]
    )
    assert len(shortened.encode("utf-16-le")) // 2 > 260
    service = CoreAutostart(long_windows_command)
    with pytest.raises(ManagementError, match="path is too long"):
        service.set_enabled(True)
    assert registry.values == original and not registry.writes
    assert not script.exists()
    assert service.status()["state"] == "stale"
    assert service.set_enabled(False)["state"] == "disabled"
    assert registry.values == {
        name: value
        for name, value in original.items()
        if name != autostart.CORE_RUN_VALUE
    }


def test_windows_permissions_and_unpersisted_credentials_are_safe(
    monkeypatch, windows_command
):
    command = windows_command
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    service = CoreAutostart(
        StartupCommand(command.args, command.directory, "Save token_file first")
    )
    with pytest.raises(ManagementError, match="Save token_file"):
        service.set_enabled(True)
    assert not registry.writes
    service.set_enabled(False)  # Disabling never requires launch credentials.
    monkeypatch.setattr(
        registry,
        "SetValueEx",
        lambda *args: (_ for _ in ()).throw(PermissionError("PRIVATE")),
    )
    with pytest.raises(ManagementError, match="check permissions") as error:
        CoreAutostart(command).set_enabled(True)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "outdated", ["command", "registry-type", "environment", "working-directory"]
)
def test_windows_stale_entry_is_reported_and_can_be_repaired_or_removed(
    monkeypatch, windows_command, outdated
):
    command = windows_command
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    registry.values["Chat2Local"] = ("existing desktop startup", registry.REG_SZ)
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    command = StartupCommand(
        (command.args[0], "-m", "chat2local.main"), command.directory
    )
    service = CoreAutostart(command)
    service.set_enabled(True)
    if outdated == "command":
        registry.values[autostart.CORE_RUN_VALUE] = (
            "old-python.exe -m chat2local.main hub",
            registry.REG_SZ,
        )
    elif outdated == "registry-type":
        registry.values[autostart.CORE_RUN_VALUE] = (
            registry.values[autostart.CORE_RUN_VALUE][0],
            4,
        )
    elif outdated == "environment":
        Path(command.args[0]).unlink()
    else:
        command.directory.rmdir()
    assert service.status()["state"] == "stale"
    assert service.status()["enabled"] is None
    if outdated == "environment":
        Path(command.args[0]).touch()
    elif outdated == "working-directory":
        command.directory.mkdir()
    assert service.set_enabled(True)["state"] == "enabled"
    assert registry.values[autostart.CORE_RUN_VALUE][0] == subprocess.list2cmdline(
        command.args
    )
    assert service.set_enabled(False)["state"] == "disabled"
    assert registry.values["Chat2Local"] == (
        "existing desktop startup",
        registry.REG_SZ,
    )


@pytest.mark.parametrize("damage", ["missing", "changed", "old-context"])
def test_windows_long_startup_script_is_validated_and_repaired(
    monkeypatch, long_windows_command, damage
):
    command = long_windows_command
    platform(monkeypatch, "win32")
    registry = FakeRegistry()
    monkeypatch.setattr(WindowsRunEntry, "_registry", lambda _: registry)
    service = CoreAutostart(command)
    service.set_enabled(True)
    script = autostart.config.user_data_directory() / "windows-core-startup.py"
    if damage == "missing":
        script.unlink()
    elif damage == "changed":
        script.write_text("print('unrelated script')")
    else:
        service.command = StartupCommand((*command.args, "--debug"), command.directory)
    assert service.status()["state"] == "stale"
    assert service.set_enabled(True)["state"] == "enabled"
    assert service.set_enabled(False)["state"] == "disabled"


class Systemd:
    def __init__(self, *, unit="custom-hub.service", scope="system", installed=True):
        self.unit, self.scope = unit, scope
        self.installed, self.enabled = installed, False
        self.calls = []
        self.path = "/etc/systemd/system/" + unit
        self.exec_start = "/opt/chat2local/.venv/bin/python -m chat2local.main hub"
        self.fail = None

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        assert args[0] == "systemctl" and "--no-ask-password" in args
        assert ("--user" in args) == (self.scope == "user")
        assert not set(args) & {"start", "stop", "restart", "--now", "sudo"}
        if self.fail and self.fail in args:
            raise ManagementError("Startup operation failed; check service permissions and service configuration")
        if "show" in args:
            assert self.unit in args
            if not self.installed:
                return "LoadState=not-found\nUnitFileState=\n"
            return (f"LoadState=loaded\nUnitFileState={'enabled' if self.enabled else 'disabled'}\n"
                    f"FragmentPath={self.path}\nExecStart={self.exec_start}\nMainPID=123\n")
        if "daemon-reload" in args:
            self.installed = True
        if "enable" in args:
            self.enabled = True
        if "disable" in args:
            self.enabled = False
        return ""


@pytest.mark.parametrize("scope", ["user", "system"])
def test_linux_manages_real_unit_name_and_scope_without_redeploy(monkeypatch, command, scope):
    platform(monkeypatch, "linux")
    manager = Systemd(scope=scope)
    monkeypatch.setattr(autostart, "_run", manager)
    service = CoreAutostart(command, AutostartConfig(service=manager.unit, scope=scope))
    assert service.status()["service"] == manager.unit
    assert service.status()["path"] == manager.path
    assert service.set_enabled(True)["enabled"]
    assert not service.set_enabled(False)["enabled"]
    assert all("daemon-reload" not in args for args in manager.calls)


@pytest.mark.parametrize("scope,group", [
    ("system", "/system.slice/company-chat-hub.service"),
    ("user", "/user.slice/user-1000.slice/user@1000.service/app.slice/company-chat-hub.service"),
])
def test_linux_detects_current_service_from_cgroup(monkeypatch, command, scope, group):
    platform(monkeypatch, "linux")
    manager = Systemd(unit="company-chat-hub.service", scope=scope)
    monkeypatch.setattr(autostart, "_run", manager)
    original = Path.read_text
    def read(path, *args, **kwargs):
        if path == Path("/proc/self/cgroup"):
            return f"0::{group}\n"
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    state = CoreAutostart(command).status()
    assert (state["service"], state["scope"]) == (manager.unit, scope)


def test_linux_new_user_unit_has_environment_paths_mode_and_future_only_switch(monkeypatch, tmp_path, command):
    platform(monkeypatch, "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda path, *a, **kw: "0::/user.slice/session.scope\n" if path == Path("/proc/self/cgroup") else original(path, *a, **kw))
    manager = Systemd(unit=autostart.USER_UNIT, scope="user", installed=False)
    monkeypatch.setattr(autostart, "_run", manager)
    service = CoreAutostart(command)
    assert not service.status()["enabled"]
    assert service.set_enabled(True)["enabled"]
    text = service._unit_path().read_text(encoding="utf-8")
    assert "WorkingDirectory=" + json.dumps(str(command.directory), ensure_ascii=False) in text
    assert '"chat2local.main" "agent"' in text and '"--config"' in text
    assert "WantedBy=default.target" in text
    assert not service.set_enabled(False)["enabled"]
    assert service._unit_path().read_text(encoding="utf-8") == text


def test_linux_permissions_and_unrelated_services_are_not_overwritten(monkeypatch, command):
    platform(monkeypatch, "linux")
    manager = Systemd()
    monkeypatch.setattr(autostart, "_run", manager)
    service = CoreAutostart(command, AutostartConfig(service=manager.unit, scope="system"))
    manager.fail = "enable"
    with pytest.raises(ManagementError, match="permissions"):
        service.set_enabled(True)
    assert service.status()["enabled"] is False
    manager.exec_start = "/usr/sbin/sshd"
    with pytest.raises(ManagementError, match="Select an existing Chat2Local"):
        service.set_enabled(False)


@pytest.mark.parametrize("state", ["masked", "static", "indirect", "enabled-runtime"])
def test_linux_non_persistent_or_static_states_are_not_a_false_boolean(monkeypatch, command, state):
    platform(monkeypatch, "linux")
    service = CoreAutostart(command)
    monkeypatch.setattr(service, "_linux_target", lambda: ("user", "test.service", {"LoadState": "loaded", "UnitFileState": state}))
    assert service.status()["enabled"] is None
    with pytest.raises(ManagementError, match="persistent startup switch"):
        service.set_enabled(True)


def test_linux_real_masked_load_state_remains_visible_and_read_only(monkeypatch, command):
    platform(monkeypatch, "linux")
    service = CoreAutostart(command, AutostartConfig(service="selected.service", scope="system"))
    monkeypatch.setattr(service, "_show", lambda *args: {"LoadState": "masked", "UnitFileState": "masked"})
    assert service.status()["state"] == "masked"
    assert service.status()["enabled"] is None
    with pytest.raises(ManagementError, match="persistent startup switch"):
        service.set_enabled(False)


class Launchctl:
    def __init__(self):
        self.disabled = False
        self.calls = []
        self.fail = False

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        assert args[0] == "launchctl"
        assert not set(args) & {"bootout", "unload", "bootstrap", "kickstart", "sudo"}
        if "print-disabled" in args:
            return f'{{\n "{autostart.LAUNCH_LABEL}" => {str(self.disabled).lower()}\n}}'
        if self.fail:
            raise ManagementError("Startup operation failed; check service permissions and service configuration")
        self.disabled = "disable" in args
        return ""


@pytest.fixture
def mac(monkeypatch, command, tmp_path):
    platform(monkeypatch, "darwin")
    monkeypatch.setattr(autostart.os, "getuid", lambda: 501, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda _: tmp_path))
    manager = Launchctl()
    monkeypatch.setattr(autostart, "_run", manager)
    return CoreAutostart(command), manager


def test_macos_launchagent_uses_plist_argument_array_and_persistent_overrides(mac, command):
    service, manager = mac
    assert not service.status()["enabled"]
    assert service.set_enabled(True)["enabled"]
    data = plistlib.loads(service._plist_path().read_bytes())
    assert data == {"Label": autostart.LAUNCH_LABEL, "ProgramArguments": list(command.args),
                    "WorkingDirectory": str(command.directory), "RunAtLoad": True}
    assert not service.set_enabled(False)["enabled"]
    assert service._plist_path().exists()
    assert service.set_enabled(True)["enabled"]
    assert ["launchctl", "disable", "gui/501/org.chat2local.core"] in manager.calls


def test_macos_failure_rolls_back_new_file_and_preserves_existing_file(mac):
    service, manager = mac
    manager.fail = True
    with pytest.raises(ManagementError, match="permissions"):
        service.set_enabled(True)
    assert not service._plist_path().exists()
    manager.fail = False
    service.set_enabled(True)
    before = service._plist_path().read_bytes()
    manager.fail = True
    with pytest.raises(ManagementError):
        service.set_enabled(False)
    assert service._plist_path().read_bytes() == before


def test_macos_respects_plist_disabled_default_and_rejects_invalid_plist(mac, monkeypatch):
    service, _manager = mac
    service.set_enabled(True)
    data = plistlib.loads(service._plist_path().read_bytes())
    data["Disabled"] = True
    service._plist_path().write_bytes(plistlib.dumps(data))
    monkeypatch.setattr(autostart, "_run", lambda *args, **kwargs: "disabled services = {}")
    assert not service.status()["enabled"]
    service._plist_path().write_bytes(plistlib.dumps(["invalid"]))
    with pytest.raises(ManagementError, match="not an independent Chat2Local"):
        service.status()


def test_macos_enable_updates_future_context_and_rollback_preserves_original_bytes(mac, command):
    service, manager = mac
    service.set_enabled(True)
    before = service._plist_path().read_bytes()
    service.command = StartupCommand((*command.args, "--read-max-lines", "12"), command.directory)
    manager.fail = True
    with pytest.raises(ManagementError):
        service.set_enabled(True)
    assert service._plist_path().read_bytes() == before
    manager.fail = False
    assert service.set_enabled(True)["enabled"]
    assert plistlib.loads(service._plist_path().read_bytes())["ProgramArguments"][-2:] == ["--read-max-lines", "12"]


@pytest.mark.parametrize("name", ["--now.service", "a;b.service", "a*.service", "a.service\nother", "../a.service"])
def test_service_names_cannot_supply_options_patterns_or_paths(name):
    with pytest.raises(ConfigError):
        validate_persisted_config({"autostart": {"service": name}})


def test_config_defaults_and_management_port_validation():
    assert AppConfig().management.model_dump() == {"enabled": True, "port": 8767}
    assert AppConfig().browser.port == 8766
    for bad in (0, 65536, True, "8767"):
        with pytest.raises(ConfigError):
            validate_persisted_config({"management": {"port": bad}})
