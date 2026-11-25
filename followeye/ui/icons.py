from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

ACTIVE = QColor("#3EC7C2")
PAUSED = QColor("#8A8F98")


def eye_icon(state: str = "active", size: int = 64) -> QIcon:
    """state: 'active', 'paused' or 'off'."""
    color = ACTIVE if state == "active" else PAUSED
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64
    outline = QPainterPath()
    outline.moveTo(4 * s, 32 * s)
    outline.quadTo(32 * s, 4 * s, 60 * s, 32 * s)
    outline.quadTo(32 * s, 60 * s, 4 * s, 32 * s)
    p.setPen(QPen(color, 5 * s))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(outline)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(32 * s, 32 * s), 11 * s, 11 * s)
    if state == "off":
        p.setPen(QPen(color, 6 * s, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(10 * s, 54 * s), QPointF(54 * s, 10 * s))
    elif state == "paused":
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(26.5 * s, 26 * s, 4 * s, 12 * s), 1 * s, 1 * s)
        p.drawRoundedRect(QRectF(33.5 * s, 26 * s, 4 * s, 12 * s), 1 * s, 1 * s)
    p.end()
    return QIcon(pix)
