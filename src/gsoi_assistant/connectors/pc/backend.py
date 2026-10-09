"""Operating-system access, behind a small interface so the logic is testable anywhere."""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from gsoi_assistant.connectors.pc.matching import clean_name

FOLDERS = ("documents", "downloads", "desktop", "pictures", "music", "videos")
MEDIA_ACTIONS = ("play_pause", "next", "previous", "volume_up", "volume_down", "mute")
_SYSTEM_ROOT = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
_POWERSHELL = str(_SYSTEM_ROOT / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")
_EXPLORER = str(_SYSTEM_ROOT / "explorer.exe")  # absolute paths: nothing is looked up on PATH
_APP_ID = re.compile(r"^[A-Za-z0-9_.!\\-]{1,200}$")

# Windows virtual-key codes for media keys
_VK = {
    "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "next": 0xB0, "previous": 0xB1, "play_pause": 0xB3,
}  # fmt: skip


@dataclass(frozen=True)
class AppEntry:
    name: str
    target: str  # a .lnk path, or "shell:AppsFolder\\<AppUserModelID>"


class PcBackend(Protocol):
    def apps(self) -> list[AppEntry]: ...
    def launch(self, app: AppEntry) -> None: ...
    def open_url(self, url: str, browser: str | None = None) -> None: ...
    def open_folder(self, which: str) -> None: ...
    def compose_mail(self, mailto: str) -> None: ...
    def media(self, action: str) -> None: ...


def parse_start_apps(raw: str) -> list[AppEntry]:
    """Parse the JSON of PowerShell's `Get-StartApps | ConvertTo-Json` (one object or a list)."""
    try:
        data: Any = json.loads(raw) if raw.strip() else []
    except ValueError:
        return []
    items = [data] if isinstance(data, dict) else data
    seen: set[str] = set()
    out: list[AppEntry] = []
    for item in items if isinstance(items, list) else []:
        name = clean_name(str(item.get("Name", ""))) if isinstance(item, dict) else ""
        app_id = str(item.get("AppID", "")) if isinstance(item, dict) else ""
        if not name or not _APP_ID.match(app_id) or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(AppEntry(name, f"shell:AppsFolder\\{app_id}"))
    return sorted(out, key=lambda a: a.name.lower())


def scan_shortcuts(roots: list[Path]) -> list[AppEntry]:
    """Fallback index: Start Menu shortcuts."""
    seen: dict[str, AppEntry] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for lnk in root.rglob("*.lnk"):
            name = clean_name(lnk.stem)
            if name and name.lower() not in seen and "uninstall" not in name.lower():
                seen[name.lower()] = AppEntry(name, str(lnk))
    return sorted(seen.values(), key=lambda a: a.name.lower())


BROWSERS = ("chrome", "edge", "firefox", "brave", "opera")
_BROWSER_EXE = {
    "chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe",
    "brave": "brave.exe", "opera": "opera.exe",
}  # fmt: skip


def _browser_path(name: str) -> str | None:
    """Where a browser is installed (Windows 'App Paths' registry), or None."""
    exe = _BROWSER_EXE.get(name)
    if exe is None or sys.platform != "win32":
        return None
    import winreg

    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, key) as handle:
                path = str(winreg.QueryValue(handle, None))
        except OSError:
            continue
        if path and Path(path).is_file():
            return path
    return None


class WindowsBackend:
    """Implementation for Windows. Not exercised by the automated tests on other platforms."""

    def __init__(self) -> None:
        self._cache: list[AppEntry] | None = None

    def apps(self) -> list[AppEntry]:
        if self._cache is None:
            self._cache = self._index()
        return self._cache

    def _index(self) -> list[AppEntry]:
        try:
            proc = subprocess.run(  # noqa: S603  # nosec B603 B607
                [_POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
                 "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=30, creationflags=0x08000000,
            )  # fmt: skip
            apps = parse_start_apps(proc.stdout)
            if apps:
                return apps
        except (OSError, subprocess.SubprocessError):
            pass
        roots = [
            Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        ]
        return scan_shortcuts(roots)

    def launch(self, app: AppEntry) -> None:
        if app.target.startswith("shell:AppsFolder\\"):
            # target comes from the OS app list and matched _APP_ID (no spaces/quotes); list args
            subprocess.Popen([_EXPLORER, app.target])  # noqa: S603  # nosec B603
        else:
            os.startfile(app.target)  # type: ignore[attr-defined]  # noqa: S606  # nosec B606

    def open_url(self, url: str, browser: str | None = None) -> None:
        exe = _browser_path(browser) if browser else None
        if exe is None:
            webbrowser.open(url, new=2)  # the default browser
        else:
            subprocess.Popen([exe, url])  # noqa: S603  # nosec B603  # url is http(s), no shell

    def compose_mail(self, mailto: str) -> None:
        if not mailto.startswith("mailto:"):
            raise ValueError(mailto)
        os.startfile(mailto)  # type: ignore[attr-defined]  # noqa: S606  # nosec B606

    def open_folder(self, which: str) -> None:
        if which not in FOLDERS:
            raise ValueError(which)
        os.startfile(Path.home() / which.capitalize())  # type: ignore[attr-defined]  # noqa: S606  # nosec B606

    def media(self, action: str) -> None:
        import ctypes

        vk = _VK[action]
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)  # key up


def default_backend() -> PcBackend | None:
    return WindowsBackend() if sys.platform == "win32" else None
