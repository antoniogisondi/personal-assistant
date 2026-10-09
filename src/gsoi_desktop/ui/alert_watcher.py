from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from gsoi_desktop.alerts import Alert
from gsoi_desktop.ui.async_call import AsyncRunner

INTERVAL_MS = 90_000
FIRST_CHECK_MS = 20_000


class AlertWatcher(QObject):
    """Asks the internal service now and then whether there is something new to tell."""

    alerts = Signal(object)  # list[Alert]: everything new since the last check

    def __init__(
        self,
        runner: AsyncRunner,
        check: Callable[[int], list[Alert]],
        *,
        enabled: Callable[[], bool],
        lead_minutes: Callable[[], int],
        interval_ms: int = INTERVAL_MS,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._check = check
        self._enabled = enabled
        self._lead = lead_minutes
        self._in_flight = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.poll)

    def start(self, first_check_ms: int = FIRST_CHECK_MS) -> None:
        self._timer.start()
        QTimer.singleShot(first_check_ms, self.poll)

    def stop(self) -> None:
        self._timer.stop()

    def poll(self) -> None:
        if self._in_flight or not self._enabled():
            return
        self._in_flight = True
        lead = self._lead()
        self._runner.run(lambda: self._check(lead), self._done, self._failed)

    def _done(self, alerts: object) -> None:
        self._in_flight = False
        if isinstance(alerts, list) and alerts:
            self.alerts.emit(alerts)

    def _failed(self, error: Exception) -> None:
        self._in_flight = False  # the next round tries again; nothing to tell the user
