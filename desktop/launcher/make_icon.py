"""Draw the vizsup app icon (desktop/assets/vizsup.ico): the app's violet rounded square, a
white play mark and a subtitle bar. Several PNG frames in one .ico (16–256 px).

Run: backend/.venv/Scripts/python.exe desktop/launcher/make_icon.py  (offscreen, no window)"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (QColor, QGuiApplication, QImage, QLinearGradient,  # noqa: E402
                           QPainter, QPainterPath, QPolygonF)

OUT = Path(__file__).resolve().parents[1] / "assets" / "vizsup.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def frame(px: int) -> bytes:
    img = QImage(px, px, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = px / 256.0
    g = QLinearGradient(0, 0, px, px)
    g.setColorAt(0.0, QColor("#bb99ff"))
    g.setColorAt(1.0, QColor("#7a4ee0"))
    bg = QPainterPath()
    bg.addRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)
    p.fillPath(bg, g)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    play = QPolygonF([QPointF(100 * s, 62 * s), QPointF(100 * s, 150 * s), QPointF(172 * s, 106 * s)])
    p.drawPolygon(play)
    bar = QPainterPath()
    bar.addRoundedRect(QRectF(58 * s, 178 * s, 140 * s, 30 * s), 15 * s, 15 * s)
    p.fillPath(bar, QColor("#ffffff"))
    p.end()
    buf = QByteArray()
    io = QBuffer(buf)
    io.open(QIODevice.WriteOnly)
    img.save(io, "PNG")
    return bytes(buf)


def main() -> int:
    QGuiApplication(sys.argv)
    pngs = [frame(px) for px in SIZES]
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for px, data in zip(SIZES, pngs):
        dim = 0 if px >= 256 else px
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    OUT.write_bytes(head + entries + blobs)
    print(OUT, OUT.stat().st_size, "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
