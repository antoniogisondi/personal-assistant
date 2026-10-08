"""The assistant's face: a living neural network drawn with QPainter.

A sphere of neurons slowly turns; signals travel along the connections and light up nodes. The
network reacts to what the assistant is doing:

    idle       slow and dim
    listening  bright cyan, driven by the microphone level
    thinking   violet, fast cascades of signals
    speaking   teal, pulsing with the voice
    error      a short red flash
    off        grey (not set up yet)
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget


class NeuralState(Enum):
    OFF = "off"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"


STATE_LABEL = {
    NeuralState.OFF: "Non configurato",
    NeuralState.IDLE: "In attesa",
    NeuralState.LISTENING: "Ti ascolto",
    NeuralState.THINKING: "Sto pensando",
    NeuralState.SPEAKING: "Sto parlando",
    NeuralState.ERROR: "Errore",
}
_COLOR = {
    NeuralState.OFF: "#5b6475",
    NeuralState.IDLE: "#5b8cff",
    NeuralState.LISTENING: "#37c8ff",
    NeuralState.THINKING: "#a678ff",
    NeuralState.SPEAKING: "#3ddc97",
    NeuralState.ERROR: "#ff5d5d",
}
_ROTATION = {  # radians per second
    NeuralState.OFF: 0.05, NeuralState.IDLE: 0.16, NeuralState.LISTENING: 0.3,
    NeuralState.THINKING: 0.9, NeuralState.SPEAKING: 0.4, NeuralState.ERROR: 0.1,
}  # fmt: skip


@dataclass
class _Pulse:
    edge: int
    t: float
    speed: float
    forward: bool


class NeuralView(QWidget):
    clicked = Signal()  # e.g. to interrupt the assistant while it speaks

    NODES = 70
    NEIGHBOURS = 3

    def __init__(self, parent: QWidget | None = None, *, seed: int = 7, fps: int = 30) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._rng = random.Random(seed)  # noqa: S311  # nosec B311
        self._state = NeuralState.IDLE
        self._level = 0.0  # smoothed 0..1
        self._target_level = 0.0
        self._level_from_outside = False
        self._angle = 0.0
        self._clock = 0.0
        self._error_left = 0.0
        self._points = self._sphere(self.NODES)
        self._edges = self._connect(self._points, self.NEIGHBOURS)
        self._activation = [0.0] * self.NODES
        self._pulses: list[_Pulse] = []
        self._adjacent: dict[int, list[int]] = {i: [] for i in range(self.NODES)}
        for k, (a, b) in enumerate(self._edges):
            self._adjacent[a].append(k)
            self._adjacent[b].append(k)
        self._last = time.monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / fps))
        self._timer.timeout.connect(self._on_timer)
        self.setToolTip(STATE_LABEL[self._state])

    # ---- public API ---------------------------------------------------------------------

    @property
    def state(self) -> NeuralState:
        return self._state

    def set_state(self, state: NeuralState) -> None:
        if state is self._state:
            return
        self._state = state
        self._error_left = 1.2 if state is NeuralState.ERROR else 0.0
        if state not in (NeuralState.LISTENING, NeuralState.SPEAKING):
            self._target_level = 0.0
            self._level_from_outside = False
        self.setToolTip(STATE_LABEL[state])
        self.update()

    def set_level(self, level: float) -> None:
        """Real audio level (0..1) from the microphone or the voice, if known."""
        self._level_from_outside = True
        self._target_level = max(0.0, min(1.0, level))

    def tick(self, dt: float) -> None:
        """Advance the animation by `dt` seconds (public: tests drive it deterministically)."""
        dt = max(0.0, min(dt, 0.1))
        self._clock += dt
        self._angle += _ROTATION[self._state] * dt * (1.0 + 1.5 * self._level)
        if self._state is NeuralState.SPEAKING and not self._level_from_outside:
            self._target_level = 0.45 + 0.4 * math.sin(self._clock * 2 * math.pi * 3.1) ** 2
        self._level += (self._target_level - self._level) * min(1.0, dt * 12)
        if self._error_left > 0:
            self._error_left -= dt
            if self._error_left <= 0 and self._state is NeuralState.ERROR:
                self._state = NeuralState.IDLE
        self._spawn(dt)
        self._advance(dt)
        decay = math.exp(-dt * 2.6)
        self._activation = [a * decay for a in self._activation]

    def projected(self) -> list[tuple[float, float, float]]:
        """Screen-space (x, y, depth) of every node for the current rotation, in -1..1 units."""
        ca, sa = math.cos(self._angle), math.sin(self._angle)
        tilt = 0.38
        ct, st = math.cos(tilt), math.sin(tilt)
        breathe = 1.0 + 0.05 * math.sin(self._clock * 1.3) + 0.12 * self._level
        out = []
        for x, y, z in self._points:
            x1, z1 = x * ca + z * sa, -x * sa + z * ca
            y2, z2 = y * ct - z1 * st, y * st + z1 * ct
            persp = 1.0 / (1.0 - 0.28 * z2)
            out.append((x1 * persp * breathe, y2 * persp * breathe, z2))
        return out

    # ---- animation internals --------------------------------------------------------------

    @staticmethod
    def _sphere(n: int) -> list[tuple[float, float, float]]:
        golden = math.pi * (3 - math.sqrt(5))
        pts = []
        for i in range(n):
            y = 1 - 2 * (i + 0.5) / n
            r = math.sqrt(max(0.0, 1 - y * y))
            theta = golden * i
            pts.append((math.cos(theta) * r, y, math.sin(theta) * r))
        return pts

    @staticmethod
    def _connect(pts: list[tuple[float, float, float]], k: int) -> list[tuple[int, int]]:
        edges: set[tuple[int, int]] = set()
        for i, p in enumerate(pts):
            near = sorted(
                (j for j in range(len(pts)) if j != i),
                key=lambda j: sum((a - b) ** 2 for a, b in zip(p, pts[j], strict=True)),
            )[:k]
            for j in near:
                edges.add((min(i, j), max(i, j)))
        return sorted(edges)

    def _spawn_rate(self) -> float:
        s, lv = self._state, self._level
        rates = {
            NeuralState.OFF: 0.0,
            NeuralState.IDLE: 0.7,
            NeuralState.LISTENING: 2.0 + 14.0 * lv,
            NeuralState.THINKING: 16.0,
            NeuralState.SPEAKING: 3.0 + 10.0 * lv,
            NeuralState.ERROR: 0.0,
        }
        return rates[s]

    def _spawn(self, dt: float) -> None:
        expected = self._spawn_rate() * dt
        count = int(expected) + (1 if self._rng.random() < expected - int(expected) else 0)
        for _ in range(count):
            speed = self._rng.uniform(0.8, 1.6) * (
                2.2 if self._state is NeuralState.THINKING else 1.0
            )
            self._pulses.append(
                _Pulse(self._rng.randrange(len(self._edges)), 0.0, speed, self._rng.random() < 0.5)
            )
        if len(self._pulses) > 220:
            del self._pulses[: len(self._pulses) - 220]

    def _advance(self, dt: float) -> None:
        alive: list[_Pulse] = []
        for p in self._pulses:
            p.t += p.speed * dt
            if p.t < 1.0:
                alive.append(p)
                continue
            a, b = self._edges[p.edge]
            node = b if p.forward else a
            self._activation[node] = min(1.0, self._activation[node] + 0.8)
            if (
                self._state not in (NeuralState.OFF, NeuralState.ERROR)
                and self._rng.random() < 0.55
            ):
                options = [e for e in self._adjacent[node] if e != p.edge]
                if options:  # the signal continues from the node it just reached
                    e = self._rng.choice(options)
                    alive.append(_Pulse(e, 0.0, p.speed, self._edges[e][0] == node))
        self._pulses = alive

    def _on_timer(self) -> None:
        now = time.monotonic()
        self.tick(now - self._last)
        self._last = now
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._last = time.monotonic()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._timer.stop()  # no CPU use while minimized to the tray
        super().hideEvent(event)

    # ---- drawing ------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor("#0b1020"))
        cx, cy = w / 2, h / 2
        radius = min(w, h) * 0.36
        base = QColor(_COLOR[self._state])

        glow = QRadialGradient(QPointF(cx, cy), radius * 1.6)
        c0 = QColor(base)
        c0.setAlpha(int(40 + 90 * self._level + (60 if self._state is NeuralState.THINKING else 0)))
        c1 = QColor(base)
        c1.setAlpha(0)
        glow.setColorAt(0.0, c0)
        glow.setColorAt(1.0, c1)
        p.setBrush(QBrush(glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), radius * 1.6, radius * 1.6)

        pts = self.projected()

        def at(i: int) -> QPointF:
            return QPointF(cx + pts[i][0] * radius, cy + pts[i][1] * radius)

        for a, b in self._edges:  # connections (far ones fainter)
            depth = (pts[a][2] + pts[b][2]) / 2
            col = QColor(base)
            col.setAlpha(int(28 + 42 * (depth + 1) / 2))
            p.setPen(QPen(col, 1.0))
            p.drawLine(at(a), at(b))

        for pulse in self._pulses:  # travelling signals
            a, b = self._edges[pulse.edge]
            s, e = (at(a), at(b)) if pulse.forward else (at(b), at(a))
            pos = QPointF(s.x() + (e.x() - s.x()) * pulse.t, s.y() + (e.y() - s.y()) * pulse.t)
            col = QColor("#ffffff") if self._state is not NeuralState.ERROR else QColor("#ffb0b0")
            col.setAlpha(200)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(col))
            p.drawEllipse(pos, 2.2, 2.2)

        for i in sorted(range(len(pts)), key=lambda k: pts[k][2]):  # nodes, far to near
            depth = (pts[i][2] + 1) / 2
            act = self._activation[i]
            size = 2.0 + 2.6 * depth + 4.0 * act
            col = QColor(base)
            col.setAlpha(int(90 + 120 * depth + 45 * act))
            halo = QRadialGradient(at(i), size * 3.2)
            hc = QColor(base)
            hc.setAlpha(int(25 + 150 * act))
            halo.setColorAt(0.0, hc)
            hc2 = QColor(base)
            hc2.setAlpha(0)
            halo.setColorAt(1.0, hc2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(at(i), size * 3.2, size * 3.2)
            p.setBrush(QBrush(col))
            p.drawEllipse(at(i), size, size)
        p.end()
