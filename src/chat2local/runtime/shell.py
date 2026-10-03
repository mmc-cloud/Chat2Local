"""Resolve a shell and build argv; subprocess lifecycle belongs to ProcessManager."""

from dataclasses import dataclass
import shutil
import sys
from typing import Literal

ShellKind = Literal["pwsh", "powershell", "cmd", "bash", "sh", "zsh"]
POWERSHELL_UTF8_PREFIX = "try { [Console]::OutputEncoding=[System.Text.Encoding]::UTF8 } catch {}\n"


class ShellError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedShell:
    kind: ShellKind
    executable: str
    fixed_arguments: tuple[str, ...]

    def argv(self, command: str) -> tuple[str, ...]:
        if not isinstance(command, str) or "\x00" in command:
            raise ShellError("Command must be text without NUL characters")
        if self.kind in ("pwsh", "powershell"):
            command = POWERSHELL_UTF8_PREFIX + command
        elif self.kind == "cmd":
            command = "chcp 65001 >nul & " + command
        return (self.executable, *self.fixed_arguments, command)


class ShellResolver:
    def __init__(self, shell: str = "auto", *, platform: str | None = None) -> None:
        self.shell = shell
        self.platform = sys.platform if platform is None else platform

    def resolve(self) -> ResolvedShell:
        aliases = {"powershell.exe": "powershell", "cmd.exe": "cmd"}
        selected = aliases.get(self.shell, self.shell)
        if selected == "auto":
            if self.platform == "win32":
                candidates = ("pwsh", "powershell", "cmd")
            elif self.platform == "darwin":
                candidates = ("zsh", "bash", "sh")
            elif self.platform.startswith("linux"):
                candidates = ("bash", "sh")
            else:
                raise ShellError("Unsupported platform for automatic shell selection")
        elif selected in ("pwsh", "powershell", "cmd", "bash", "sh", "zsh"):
            candidates = (selected,)
        else:
            raise ShellError(f"Unsupported shell: {self.shell}")

        for kind in candidates:
            executable_name = {"powershell": "powershell.exe", "cmd": "cmd.exe"}.get(kind, kind)
            if kind == "sh" and self.platform != "win32":
                executable_name = "/bin/sh"
            executable = shutil.which(executable_name)
            if executable is not None:
                if kind in ("pwsh", "powershell"):
                    arguments = ("-NoLogo", "-NoProfile", "-Command")
                elif kind == "cmd":
                    arguments = ("/d", "/s", "/c")
                else:
                    arguments = ("-c",)
                return ResolvedShell(kind, executable, arguments)
        raise ShellError(f"Shell is unavailable: {self.shell}")
