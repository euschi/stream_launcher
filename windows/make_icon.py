"""Genera windows/icon.ico (più dimensioni, PNG incorporati) da config/icon.svg."""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

root = Path(__file__).resolve().parent.parent
app = QGuiApplication(sys.argv[:1] + ["-platform", "offscreen"])
renderer = QSvgRenderer(str(root / "config" / "icon.svg"))
images = []
for size in (16, 24, 32, 48, 64, 128, 256):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    renderer.render(painter)
    painter.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, "PNG")
    images.append((size, bytes(data)))

header = struct.pack("<HHH", 0, 1, len(images))
offset = 6 + 16 * len(images)
entries = b""
for size, png in images:
    entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset)
    offset += len(png)
(root / "windows" / "icon.ico").write_bytes(header + entries + b"".join(png for _, png in images))
print("windows/icon.ico creato")
