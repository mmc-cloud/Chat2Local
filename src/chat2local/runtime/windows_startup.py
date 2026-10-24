"""Current-user Run entries shared by the desktop and independent Core."""

import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class WindowsRunEntry:
    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def _registry(self):
        if not self.supported:
            raise ValueError("Windows login startup is not supported on this platform")
        import winreg
        return winreg

    def read(self) -> tuple[str, int] | None:
        registry = self._registry()
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_READ) as key:
                return registry.QueryValueEx(key, self.name)
        except FileNotFoundError:
            return None

    def restore(self, value: tuple[str, int] | None) -> None:
        registry = self._registry()
        if value is None:
            try:
                with registry.OpenKey(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                    registry.DeleteValue(key, self.name)
            except FileNotFoundError:
                pass
        else:
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, RUN_KEY, 0, registry.KEY_SET_VALUE) as key:
                registry.SetValueEx(key, self.name, 0, value[1], value[0])
