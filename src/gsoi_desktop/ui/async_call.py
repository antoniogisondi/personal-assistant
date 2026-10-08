"""Run blocking calls (HTTP to the local service) off the UI thread and deliver the result
back on it."""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Signal


class AsyncRunner(QObject):
    _done = Signal(int, object)
    _failed = Signal(int, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ids = itertools.count(1)
        self._callbacks: dict[int, tuple[Callable[[Any], None], Callable[[Exception], None]]] = {}
        self._done.connect(self._on_done)  # queued: emitted from worker threads
        self._failed.connect(self._on_failed)

    def run(
        self,
        fn: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> None:
        call_id = next(self._ids)
        self._callbacks[call_id] = (on_done, on_error)

        def work() -> None:
            try:
                result = fn()
            except Exception as exc:
                self._failed.emit(call_id, exc)
            else:
                self._done.emit(call_id, result)

        threading.Thread(target=work, name=f"gsoi-call-{call_id}", daemon=True).start()

    def _on_done(self, call_id: int, result: object) -> None:
        on_done, _ = self._callbacks.pop(call_id)
        on_done(result)

    def _on_failed(self, call_id: int, error: object) -> None:
        _, on_error = self._callbacks.pop(call_id)
        on_error(error if isinstance(error, Exception) else Exception(str(error)))
