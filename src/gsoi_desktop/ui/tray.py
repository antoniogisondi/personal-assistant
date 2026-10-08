from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget


class Tray:
    def __init__(
        self,
        icon: QIcon,
        window: QWidget,
        *,
        on_briefing: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        self._window = window
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self._tray = QSystemTrayIcon(icon)
        self._tray.setToolTip("GSOI")
        self.menu = QMenu()
        open_action = QAction("Apri GSOI", self.menu)
        open_action.triggered.connect(self.show_window)
        briefing_action = QAction("Riepilogo di oggi", self.menu)

        def briefing() -> None:
            self.show_window()
            on_briefing()

        briefing_action.triggered.connect(briefing)
        quit_action = QAction("Esci", self.menu)
        quit_action.triggered.connect(on_quit)
        for a in (open_action, briefing_action, quit_action):
            self.menu.addAction(a)
        self._tray.setContextMenu(self.menu)
        self._tray.activated.connect(self._activated)
        if self.available:
            self._tray.show()

    def show_window(self) -> None:
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self._window.isVisible():
                self._window.hide()
            else:
                self.show_window()

    def notify(self, title: str, message: str) -> None:
        if self.available:
            self._tray.showMessage(title, message)

    def hide(self) -> None:
        self._tray.hide()
