from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap


def make_icon() -> QIcon:
    """A simple generated icon (no image files to ship)."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor("#2457d6")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(size / 2, size / 2), size * 0.47, size * 0.47)
        p.setPen(QColor("white"))
        font = QFont("Segoe UI")
        font.setBold(True)
        font.setPixelSize(max(8, int(size * 0.5)))
        p.setFont(font)
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "G")
        p.end()
        icon.addPixmap(pix)
    return icon
