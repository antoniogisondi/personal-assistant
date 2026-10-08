"""Start with Windows: a per-user Run registry entry (no admin rights needed)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any, Protocol

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "GSOI"


class Registry(Protocol):
    def read(self, key: str, name: str) -> str | None: ...
    def write(self, key: str, name: str, value: str) -> None: ...
    def remove(self, key: str, name: str) -> None: ...


class WindowsRegistry:
    def __init__(self) -> None:
        import winreg

        self._w: Any = winreg

    def read(self, key: str, name: str) -> str | None:
        try:
            with self._w.OpenKey(self._w.HKEY_CURRENT_USER, key) as k:
                return str(self._w.QueryValueEx(k, name)[0])
        except OSError:
            return None

    def write(self, key: str, name: str, value: str) -> None:
        with self._w.CreateKey(self._w.HKEY_CURRENT_USER, key) as k:
            self._w.SetValueEx(k, name, 0, self._w.REG_SZ, value)

    def remove(self, key: str, name: str) -> None:
        try:
            with self._w.OpenKey(self._w.HKEY_CURRENT_USER, key, 0, self._w.KEY_SET_VALUE) as k:
                self._w.DeleteValue(k, name)
        except OSError:
            pass


def launch_command() -> str:
    """Command line that starts the app minimized to the tray."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    return f'"{sys.executable}" -m gsoi_desktop --minimized'


class Autostart:
    def __init__(
        self,
        registry: Registry | None = None,
        command: Callable[[], str] = launch_command,
    ) -> None:
        self._registry = registry
        self._command = command

    @property
    def supported(self) -> bool:
        return self._registry is not None or sys.platform == "win32"

    def _reg(self) -> Registry:
        if self._registry is None:
            self._registry = WindowsRegistry()
        return self._registry

    def is_enabled(self) -> bool:
        if not self.supported:
            return False
        return self._reg().read(RUN_KEY, VALUE_NAME) == self._command()

    def set_enabled(self, enabled: bool) -> None:
        if not self.supported:
            return
        if enabled:
            self._reg().write(RUN_KEY, VALUE_NAME, self._command())
        else:
            self._reg().remove(RUN_KEY, VALUE_NAME)
