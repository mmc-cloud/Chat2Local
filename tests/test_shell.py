"""Cross-platform shell resolution is tested without installing other shells."""

import os

import pytest

from chat2local.runtime import shell as module
from chat2local.runtime.process_manager import build_process_environment
from chat2local.runtime.shell import POWERSHELL_UTF8_PREFIX, ShellError, ShellResolver


@pytest.mark.parametrize("platform,available,expected,calls", [
    ("win32", {"pwsh", "powershell.exe", "cmd.exe"}, "pwsh", ["pwsh"]),
    ("win32", {"powershell.exe", "cmd.exe"}, "powershell", ["pwsh", "powershell.exe"]),
    ("win32", {"cmd.exe"}, "cmd", ["pwsh", "powershell.exe", "cmd.exe"]),
    ("linux", {"bash", "/bin/sh"}, "bash", ["bash"]),
    ("linux", {"/bin/sh"}, "sh", ["bash", "/bin/sh"]),
    ("darwin", {"zsh", "bash", "/bin/sh"}, "zsh", ["zsh"]),
    ("darwin", {"bash", "/bin/sh"}, "bash", ["zsh", "bash"]),
    ("darwin", {"/bin/sh"}, "sh", ["zsh", "bash", "/bin/sh"]),
])
def test_auto_order(platform, available, expected, calls, monkeypatch):
    seen = []
    def which(name):
        seen.append(name)
        return "/resolved/" + name if name in available else None
    monkeypatch.setattr(module.shutil, "which", which)
    selected = ShellResolver(platform=platform).resolve()
    assert selected.kind == expected and seen == calls
    assert selected.executable == "/resolved/" + calls[-1]


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_auto_no_shell_fails_and_never_selects_wsl_git_bash_or_msys(platform, monkeypatch):
    attempted = []
    def which(name):
        attempted.append(name)
        if platform == "win32" and name in ("bash", "wsl", "git-bash", "msys2", "cygwin"):
            return "/wrong/" + name
        return None
    monkeypatch.setattr(module.shutil, "which", which)
    with pytest.raises(ShellError, match="unavailable"):
        ShellResolver(platform=platform).resolve()
    assert not any(name in attempted for name in ("wsl", "git-bash", "msys2", "cygwin"))
    if platform == "win32":
        assert "bash" not in attempted


@pytest.mark.parametrize("name,kind,args,prefix", [
    ("pwsh", "pwsh", ("-NoLogo", "-NoProfile", "-Command"), POWERSHELL_UTF8_PREFIX),
    ("powershell", "powershell", ("-NoLogo", "-NoProfile", "-Command"), POWERSHELL_UTF8_PREFIX),
    ("powershell.exe", "powershell", ("-NoLogo", "-NoProfile", "-Command"), POWERSHELL_UTF8_PREFIX),
    ("cmd", "cmd", ("/d", "/s", "/c"), "chcp 65001 >nul & "),
    ("cmd.exe", "cmd", ("/d", "/s", "/c"), "chcp 65001 >nul & "),
    ("bash", "bash", ("-c",), ""), ("sh", "sh", ("-c",), ""), ("zsh", "zsh", ("-c",), ""),
])
def test_explicit_shell_args_and_command_is_preserved(name, kind, args, prefix, monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/shell/" + name)
    selected = ShellResolver(name, platform="linux").resolve()
    command = "echo 'hello'; echo \"你好\"\n# untouched"
    assert selected.kind == kind and selected.fixed_arguments == args
    assert selected.argv(command) == (selected.executable, *args, prefix + command)


def test_explicit_unavailable_never_falls_back(monkeypatch):
    calls = []
    def which(name):
        calls.append(name)
        return "/cmd.exe" if name == "cmd.exe" else None
    monkeypatch.setattr(module.shutil, "which", which)
    with pytest.raises(ShellError, match="unavailable"):
        ShellResolver("pwsh", platform="win32").resolve()
    assert calls == ["pwsh"]


@pytest.mark.parametrize("name", ["wsl", "fish", "/missing/bash", "", "git-bash", "msys2"])
def test_unsupported_shell_fails(name):
    with pytest.raises(ShellError, match="Unsupported shell"):
        ShellResolver(name).resolve()


def test_unsupported_auto_platform_fails():
    with pytest.raises(ShellError, match="Unsupported platform"):
        ShellResolver(platform="unknown").resolve()


def test_invalid_command_fails(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda name: "/shell")
    with pytest.raises(ShellError):
        ShellResolver("bash").resolve().argv("bad\x00")


@pytest.mark.parametrize("platform,locale", [("win32", None), ("linux", "C.UTF-8"), ("darwin", "en_US.UTF-8")])
def test_environment_inherits_and_overrides_only_stable_settings(platform, locale, monkeypatch):
    original = {
        "PATH": "/user/path", "HOME": "/user/home", "USERPROFILE": "user-profile", "VIRTUAL_ENV": "user-venv",
        "HTTP_PROXY": "proxy-a", "HTTPS_PROXY": "proxy-b", "ARBITRARY_USER_VALUE": "keep",
        "NO_COLOR": "0", "COLORTERM": "color", "PATHEXT": ".COM;.EXE;.FOO",
    }
    monkeypatch.setattr(os, "environ", original)
    result = build_process_environment(platform=platform)
    for key in ("PATH", "HOME", "USERPROFILE", "VIRTUAL_ENV", "HTTP_PROXY", "HTTPS_PROXY", "ARBITRARY_USER_VALUE"):
        assert result[key] == original[key]
    assert result["NO_COLOR"] == "1" and result["COLORTERM"] == ""
    assert result["GIT_PAGER"] == result["GH_PAGER"] == "cat"
    assert "CI" not in result and "CODEX_CI" not in result
    if locale:
        assert result["LANG"] == result["LC_CTYPE"] == result["LC_ALL"] == locale
        assert result["TERM"] == "dumb" and result["PAGER"] == "cat"
    else:
        assert not {"LANG", "LC_ALL", "TERM", "PAGER"} & result.keys()
    assert original["NO_COLOR"] == "0"  # never mutate server os.environ


@pytest.mark.parametrize("original,expected", [
    (None, ".COM;.EXE;.BAT;.CMD"), ("", ".COM;.EXE;.BAT;.CMD"),
    (".COM;.exe;.CUSTOM", ".COM;.exe;.CUSTOM"),
    (".MYEXT;.BAT", ".MYEXT;.BAT;.EXE"), (".MYEXT;", ".MYEXT;.EXE"),
])
def test_windows_pathext_repair_preserves_user_extensions(original, expected, monkeypatch):
    import os
    environment = {} if original is None else {"PATHEXT": original}
    monkeypatch.setattr(os, "environ", environment)
    assert build_process_environment(platform="win32")["PATHEXT"] == expected


def test_existing_ci_flags_are_inherited_not_removed(monkeypatch):
    import os
    monkeypatch.setattr(os, "environ", {"CI": "user-choice", "CODEX_CI": "user-choice"})
    result = build_process_environment(platform="linux")
    assert result["CI"] == result["CODEX_CI"] == "user-choice"
