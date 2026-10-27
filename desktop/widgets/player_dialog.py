"""In-app player for the project library: one project's videos (bản dựng / bản xem
nhanh / video gốc, a split video's parts) in the editor's VideoPanel, a switch between
them, and "Mở bằng trình phát của máy" for the system player.

Closing ALWAYS unloads the player: Windows keeps a playing file locked, and the library
may move that file to the Recycle Bin next."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QVBoxLayout

from desktop.widgets.chrome import button, icon_button, label
from desktop.widgets.recent_projects import short_title
from desktop.widgets.video_panel import VideoPanel


def open_external(path) -> None:
    """The system's default player / folder window."""
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


class PlayerDialog(QDialog):
    def __init__(self, parent, title: str, videos: list[tuple[str, Path]]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Xem video — {short_title(title)}")
        self.resize(1040, 820)
        self._videos = list(videos)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        t = label("", "h2")
        t.setText(t.fontMetrics().elidedText(short_title(title), Qt.ElideRight, 520))
        t.setToolTip(title)
        head.addWidget(t)
        head.addStretch(1)
        self.pick = QComboBox()
        for name, path in self._videos:
            self.pick.addItem(name, str(path))
        self.pick.setVisible(len(self._videos) > 1)
        self.pick.setToolTip("Chọn bản để xem")
        self.pick.currentIndexChanged.connect(self._load_current)
        head.addWidget(self.pick)
        ext = button("Mở bằng trình phát của máy", "MonitorPlay", variant="ghost", small=True)
        ext.clicked.connect(lambda: self.current() and open_external(self.current()))
        head.addWidget(ext)
        folder = icon_button("FolderOpen", "Mở thư mục chứa video")
        folder.clicked.connect(lambda: self.current() and open_external(self.current().parent))
        head.addWidget(folder)
        for b in (ext, folder):
            b.setFocusPolicy(Qt.NoFocus)          # Space = play / pause, not "click"
        root.addLayout(head)

        self.panel = VideoPanel(self._videos[0][0] if self._videos else "Video", scrub=True)
        self.panel.set_viewer_mode()
        root.addWidget(self.panel, 1)
        root.addWidget(label("Space: phát / dừng · Esc: đóng", "small"), 0, Qt.AlignRight)
        if self._videos:
            self._load_current()

    def current(self) -> Path | None:
        data = self.pick.currentData()
        return Path(data) if data else None

    def _load_current(self, *_a) -> None:
        path = self.current()
        if path is None:
            return
        self.panel.title.setText(self.pick.currentText())
        self.panel.load(path)
        self.panel.play()

    def keyPressEvent(self, ev) -> None:  # noqa: N802
        if ev.key() == Qt.Key_Space:
            self.panel.toggle()
            return
        super().keyPressEvent(ev)

    def done(self, r: int) -> None:
        self.panel.unload()
        super().done(r)
