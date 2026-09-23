"""Bounded, ephemeral display copies. No network, project writes or scientific decisions."""

from collections import OrderedDict
from pathlib import Path

from stimulus_harvester.storage import resolve_path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QIcon, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QListView


class Result(QObject):
    ready = Signal(object, object)


class ReadPage(QRunnable):
    def __init__(self, jobs, result):
        super().__init__()
        self.jobs, self.result = jobs, result

    def run(self):
        for key in self.jobs:
            reader = QImageReader(key[0])
            size = reader.size()
            # Bound decoding as well as the displayed copy; mirrors Pillow's bomb limit.
            if size.isValid() and size.width() * size.height() <= 89_478_485:
                reader.setScaledSize(size.scaled(160, 110, Qt.AspectRatioMode.KeepAspectRatio))
                image = reader.read()
            else:
                image = QImage()
            self.result.ready.emit(key, image)


class ThumbnailGrid(QListWidget):
    """At most 48 items per page and 192 cached images; one active page decoder."""

    chosen = Signal(str)
    marked = Signal(str, bool)
    page_size = 48
    cache_limit = 192

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setIconSize(QSize(132, 88))
        self.setGridSize(QSize(148, 144))
        self.setWordWrap(True)
        self.setMinimumSize(160, 155)
        self.setAccessibleName("Thumbnail page. Arrows select; Space toggles batch selection")
        self.cache = OrderedDict()
        self.markable = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.result = Result(self)
        self.result.ready.connect(self.received)
        self.currentItemChanged.connect(self.selected)
        self.itemChanged.connect(self.checked)

    def selected(self, current, previous):
        if current:
            self.chosen.emit(current.data(Qt.ItemDataRole.UserRole))

    def checked(self, item):
        self.marked.emit(
            item.data(Qt.ItemDataRole.UserRole), item.checkState() == Qt.CheckState.Checked
        )

    def fill(self, rows, selected, marked, root):
        self.pool.clear()  # Drop obsolete queued pages; at most one old page is running.
        self.blockSignals(True)
        self.clear()
        jobs = []
        for row in rows:
            cid = row["candidate_id"]
            item = QListWidgetItem(
                f"{row.get('title') or cid}\n{row.get('review_text', 'Unreviewed')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setToolTip(
                f"{row.get('title') or cid}\n{row.get('width') or '?'} × {row.get('height') or '?'}"
                f" · {row.get('availability', 'Unavailable')}\n"
                + "; ".join(
                    f"{s['provider']}: {s.get('query') or 'No phrase'}" for s in row["sources"]
                )
                + "\nCheckboxes select a batch; they do not record a review."
            )
            if self.markable and row.get("displayable", row.get("available", False)):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if cid in marked else Qt.CheckState.Unchecked
                )
            key = None
            if (
                root
                and row.get("file_path")
                and row.get("displayable", row.get("available", False))
            ):
                try:
                    path = resolve_path(Path(root), row["file_path"])
                    stat = path.stat()
                    key = (str(path), stat.st_size, stat.st_mtime_ns)
                except (OSError, ValueError):
                    pass
            item.setData(Qt.ItemDataRole.UserRole + 1, key)
            if key in self.cache:
                self.cache.move_to_end(key)
                item.setIcon(QIcon(QPixmap.fromImage(self.cache[key])))
            elif key:
                jobs.append(key)
            self.addItem(item)
            if cid == selected:
                self.setCurrentItem(item)
        self.blockSignals(False)
        if jobs:
            self.pool.start(ReadPage(list(dict.fromkeys(jobs)), self.result))

    def received(self, key, image):
        self.cache[key] = image
        self.cache.move_to_end(key)
        while len(self.cache) > self.cache_limit:
            self.cache.popitem(last=False)
        for i in range(self.count()):
            item = self.item(i)
            if item.data(Qt.ItemDataRole.UserRole + 1) == key:
                self.blockSignals(True)
                item.setIcon(QIcon(QPixmap.fromImage(image)))
                if image.isNull():
                    item.setToolTip(item.toolTip() + "\nPreview unavailable; inspect for details.")
                self.blockSignals(False)

    def shutdown(self):
        self.pool.clear()
        self.pool.waitForDone()
