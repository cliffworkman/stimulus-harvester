"""Palette-aware presentation components. Images are display copies; boxes are overlays."""

import json

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QSplitter,
    QComboBox,
    QCheckBox,
    QTreeWidget,
    QTreeWidgetItem,
)


def text_label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def button(layout, text, callback):
    value = QPushButton(text)
    value.clicked.connect(callback)
    layout.addWidget(value)
    return value


def details():
    value = QPlainTextEdit()
    value.setReadOnly(True)
    value.setMinimumHeight(90)
    return value


def show_json(widget, value):
    widget.setPlainText(json.dumps(value, indent=2, ensure_ascii=False, default=str))


class ImageOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.observation = None
        self.show_boxes = False
        self.setMinimumSize(240, 180)
        self.setAccessibleName("Candidate preview with optional saved detection boxes")

    def set_image(self, content):
        self.image = QImage.fromData(content) if content else QImage()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if self.image.isNull():
            painter.setPen(self.palette().text().color())
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "No acquired image to preview"
            )
            return
        size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
        rect = QRectF(
            (self.width() - size.width()) / 2,
            (self.height() - size.height()) / 2,
            size.width(),
            size.height(),
        )
        painter.drawImage(rect, self.image)
        if self.show_boxes and self.observation:
            raw = self.observation.get("raw", {})
            width, height = raw.get("width", 0), raw.get("height", 0)
            if width and height:
                for detection in raw.get("detections", []):
                    box = detection.get("box_xyxy")
                    if not box or len(box) != 4:
                        continue
                    x1, y1, x2, y2 = box
                    target = QRectF(
                        rect.x() + x1 / width * rect.width(),
                        rect.y() + y1 / height * rect.height(),
                        (x2 - x1) / width * rect.width(),
                        (y2 - y1) / height * rect.height(),
                    )
                    # Contrasting double stroke remains legible over both bright and dark images.
                    painter.setPen(QPen(QColor("black"), 4))
                    painter.drawRect(target)
                    painter.setPen(QPen(QColor("yellow"), 2))
                    painter.drawRect(target)
                    painter.drawText(target.topLeft(), f"person {detection.get('score', '?')}")


class Inspector(QWidget):
    def __init__(self):
        super().__init__()
        self.current = None
        box = QVBoxLayout(self)
        self.identity = text_label("Select a candidate to inspect its image and evidence.")
        box.addWidget(self.identity)
        self.image = ImageOverlay()
        box.addWidget(self.image, 2)
        row = QHBoxLayout()
        self.observations = QComboBox()
        self.observations.setAccessibleName("Saved observation to inspect")
        self.boxes = QCheckBox("Show saved person boxes (display overlay)")
        row.addWidget(self.observations, 1)
        row.addWidget(self.boxes)
        box.addLayout(row)
        self.observation_summary = text_label()
        box.addWidget(self.observation_summary)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["Source / relationship", "Claim / value"])
        self.sources.setMinimumHeight(130)
        self.sources.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        box.addWidget(self.sources, 1)
        toggle = QCheckBox("Advanced evidence and complete review history")
        box.addWidget(toggle)
        self.evidence = details()
        self.evidence.hide()
        box.addWidget(self.evidence, 1)
        toggle.toggled.connect(self.evidence.setVisible)
        self.observations.currentIndexChanged.connect(self.observation_changed)
        self.boxes.toggled.connect(self.overlay_changed)

    def display(self, value):
        self.current = value
        evidence = value["evidence"]
        self.identity.setText(
            f"{evidence['primary_local_id']} · {value['candidate_id']}\nAsset: {evidence['asset_id'] or 'not acquired'}"
        )
        self.image.set_image(value["preview"])
        self.sources.clear()
        for source in evidence["sources"]:
            root = QTreeWidgetItem([source["provider"], source["source_id"]])
            for key in (
                "query",
                "creator",
                "license",
                "license_url",
                "source_page_url",
                "image_url",
            ):
                root.addChild(
                    QTreeWidgetItem([key.replace("_", " "), str(source.get(key) or "Not supplied")])
                )
            self.sources.addTopLevelItem(root)
        group = QTreeWidgetItem(
            ["Shared exact asset", f"{len(evidence['shared_candidates'])} candidate histories"]
        )
        for c in evidence["shared_candidates"]:
            group.addChild(QTreeWidgetItem([c["primary_local_id"], c["label"] or "unrated"]))
        self.sources.addTopLevelItem(group)
        self.sources.addTopLevelItem(
            QTreeWidgetItem(
                [
                    "Near duplicate (informational)",
                    str(evidence["near_duplicate"].get("duplicate_of_local_id") or "None recorded"),
                ]
            )
        )
        self.sources.addTopLevelItem(
            QTreeWidgetItem(
                [
                    "Review history",
                    f"{len(evidence['review_events'])} append-only events (advanced evidence)",
                ]
            )
        )
        self.observations.blockSignals(True)
        self.observations.clear()
        self.observations.addItem("No observation overlay", None)
        for obs in reversed(evidence["screening"]["observations"]):
            self.observations.addItem(
                f"{obs['source']} · {obs['status']} · {obs['timestamp']}", obs
            )
        self.observations.setCurrentIndex(1 if self.observations.count() > 1 else 0)
        self.observations.blockSignals(False)
        self.observation_changed()
        show_json(self.evidence, evidence)

    def observation_changed(self):
        obs = self.observations.currentData()
        matching_asset = bool(
            obs and self.current and obs["asset_id"] == self.current["evidence"]["asset_id"]
        )
        self.image.observation = obs if matching_asset else None
        if obs:
            self.observation_summary.setText(
                f"{obs['status']} · score {obs['score']} · thresholds {obs['thresholds']}\nModel: {obs['model_identity']}\nnot_detected ≠ confirmed absence; rules determine disposition."
            )
            if not matching_asset:
                self.observation_summary.setText(
                    self.observation_summary.text()
                    + "\nHistorical observation for another asset: overlay disabled."
                )
        else:
            self.observation_summary.setText(
                "No saved observation selected. No model inference is run by this viewer."
            )
        self.image.update()

    def overlay_changed(self, checked):
        self.image.show_boxes = checked
        self.image.update()


class CandidateBrowser(QWidget):
    inspect = Signal(str)

    def __init__(self):
        super().__init__()
        box = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText(
            "Find candidate, provider, query, decision or license claim…"
        )
        self.filter.setAccessibleName("Filter candidates")
        box.addWidget(self.filter)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Candidate",
                "Review",
                "Screening",
                "Providers / queries",
                "Source license claims",
                "Acquisition / size",
                "Shared asset",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((145, 100, 145, 235, 180, 175, 100)):
            self.table.setColumnWidth(column, width)
        self.table.setMinimumHeight(150)
        box.addWidget(self.table, 1)
        self.table.itemSelectionChanged.connect(self.selected)
        self.filter.textChanged.connect(self.filter_rows)

    def populate(self, candidates):
        prior = self.candidate_id()
        self.table.blockSignals(True)
        self.table.setRowCount(len(candidates))
        for i, candidate in enumerate(candidates):
            sources = candidate["sources"]
            values = [
                candidate["primary_local_id"],
                candidate["label"] or "unrated",
                candidate["screening"]["effective_disposition"],
                "; ".join(sorted({str(s["provider"]) + ": " + str(s["query"]) for s in sources})),
                "; ".join(sorted({str(s["license"] or "unknown") for s in sources})),
                f"{candidate['download_status'] or 'not acquired'} · {candidate['width'] or '?'}×{candidate['height'] or '?'}",
                str(candidate["shared_asset_candidates"]),
            ]
            for j, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, candidate["candidate_id"])
                item.setToolTip(candidate["candidate_id"] + "\n" + text)
                self.table.setItem(i, j, item)
            if candidate["candidate_id"] == prior:
                self.table.selectRow(i)
        self.table.blockSignals(False)
        self.filter_rows()

    def candidate_id(self):
        row = self.table.currentRow()
        return (
            self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            if row >= 0 and self.table.item(row, 0)
            else None
        )

    def selected(self):
        cid = self.candidate_id()
        if cid:
            self.inspect.emit(cid)

    def select_candidate(self, candidate_id):
        self.filter.clear()
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == candidate_id:
                self.table.selectRow(i)
                self.table.scrollToItem(self.table.item(i, 0))
                return

    def filter_rows(self):
        term = self.filter.text().casefold()
        for i in range(self.table.rowCount()):
            self.table.setRowHidden(
                i,
                not any(
                    term in self.table.item(i, j).text().casefold()
                    for j in range(self.table.columnCount())
                ),
            )


def candidate_split(browser, inspector):
    splitter = QSplitter(Qt.Orientation.Vertical)
    splitter.addWidget(browser)
    splitter.addWidget(inspector)
    splitter.setSizes([220, 440])
    return splitter
