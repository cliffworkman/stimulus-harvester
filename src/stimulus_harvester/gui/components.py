"""Palette-aware presentation components. Images are display copies; boxes are overlays."""

import json

from PySide6.QtCore import QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stimulus_harvester.curation import (
    FAILURES,
    plain_metadata,
    review_history_text,
    safe_link,
    screening_label,
    title_for,
)
from .thumbnails import ThumbnailGrid


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


def scroll_panel(widget):
    """Keep a long inactive settings tab from imposing its height on image review."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


def disposition_text(row):
    return {
        "review_required": "Needs human review",
        "eligible": "Eligible under screening rules",
        "excluded": "Excluded by screening",
    }.get((row.get("screening") or {}).get("effective_disposition"), "Not evaluated")


class ImageOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.observation = None
        self.show_boxes = False
        self.setMinimumSize(200, 120)
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
        self.setMinimumWidth(200)
        self.current = None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        self.identity = text_label("Select an image to see its source and history.")
        self.identity.setMaximumHeight(55)
        box.addWidget(self.identity)
        self.image = ImageOverlay()
        box.addWidget(self.image, 3)
        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        self.tabs.setMinimumSize(180, 110)
        box.addWidget(self.tabs, 2)
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(["Found through / source", "Reported value"])
        self.sources.setColumnWidth(0, 145)
        self.sources.setColumnWidth(1, 300)
        self.sources.itemActivated.connect(self.open_source)
        self.sources.setAccessibleName(
            "Source evidence. Activate a source link with Enter or double click"
        )
        self.tabs.addTab(self.sources, "Source")
        self.licenses = details()
        self.tabs.addTab(self.licenses, "License")
        screening = QWidget()
        layout = QVBoxLayout(screening)
        self.observation_summary = text_label()
        layout.addWidget(self.observation_summary)
        self.rule_summary = text_label()
        layout.addWidget(self.rule_summary)
        self.observations = QComboBox()
        self.observations.setAccessibleName("Saved screening result to inspect")
        layout.addWidget(self.observations)
        self.boxes = QCheckBox("Show saved detection boxes")
        layout.addWidget(self.boxes)
        layout.addStretch()
        self.tabs.addTab(scroll_panel(screening), "Screening")
        self.history = details()
        self.tabs.addTab(self.history, "Review history")
        technical = QWidget()
        layout = QVBoxLayout(technical)
        self.evidence = details()
        layout.addWidget(self.evidence)
        button(
            layout,
            "Copy raw evidence",
            lambda: QApplication.clipboard().setText(self.evidence.toPlainText()),
        )
        self.tabs.addTab(scroll_panel(technical), "Technical")
        self.observations.currentIndexChanged.connect(self.observation_changed)
        self.boxes.toggled.connect(self.overlay_changed)

    def clear(self):
        self.current = None
        self.identity.setText("Select an image to see its source and history.")
        self.image.set_image(b"")
        self.sources.clear()
        self.licenses.clear()
        self.history.clear()
        self.evidence.clear()
        self.observations.clear()
        self.rule_summary.clear()
        self.image.observation = None

    def open_source(self, item, column):
        url = item.data(1, Qt.ItemDataRole.UserRole)
        if safe_link(url):
            QDesktopServices.openUrl(QUrl(url))

    def display(self, value):
        self.current = value
        evidence = value["evidence"]
        img = evidence.get("image", {})
        review = evidence.get("review", {})
        observations = evidence["screening"]["observations"]
        latest = {o["source"]: o for o in observations if o["asset_id"] == evidence["asset_id"]}
        current_obs = list(latest.values())
        name = title_for(dict(img, sources=evidence["sources"]))
        decision = {"accepted": "Accepted", "maybe": "Maybe", "rejected": "Rejected"}.get(
            review.get("label"), "Unreviewed"
        )
        self.identity.setText(
            f"{name[:140]}\n{img.get('width') or '?'} × {img.get('height') or '?'} · {'Available' if value['preview'] else 'Unavailable'} · {decision}\nScreening: {screening_label(current_obs)}"
        )
        self.identity.setToolTip(name)
        self.image.set_image(value["preview"])
        self.sources.clear()
        claims = []
        for source in evidence["sources"]:
            root = QTreeWidgetItem(
                [source["provider"], source.get("query") or "Search phrase not supplied"]
            )
            for key, label in (
                ("creator", "Creator"),
                ("source_page_url", "Source page (open)"),
                ("license", "License claim"),
                ("license_url", "License link (open)"),
                ("image_url", "Media URL (open)"),
            ):
                raw = source.get(key)
                text = plain_metadata(raw) if key == "creator" else str(raw or "Not supplied")
                child = QTreeWidgetItem([label, text])
                child.setToolTip(1, text)
                if key.endswith("url") and safe_link(raw):
                    child.setData(1, Qt.ItemDataRole.UserRole, raw)
                root.addChild(child)
            self.sources.addTopLevelItem(root)
            root.setExpanded(True)
            claims.append(
                f"{source['provider']} · {source.get('query') or 'Search phrase not supplied'}\nExact claim: {source.get('license') or 'Not supplied'}\nLink: {source.get('license_url') or 'Not supplied'}"
            )
        other_sources = [
            source
            for other in evidence["shared_candidates"]
            if other["candidate_id"] != value["candidate_id"]
            for source in other.get("sources", [])
        ]
        for source in other_sources:
            claims.append(
                f"Other exact-byte history — {source['provider']} · {source.get('query') or 'Search phrase not supplied'}\n"
                f"Exact claim: {source.get('license') or 'Not supplied'}\nLink: {source.get('license_url') or 'Not supplied'}"
            )
        exact = {s.get("license") for s in evidence["sources"] + other_sources}
        warning = (
            "Multiple differing claims: they may conflict. None has been chosen as authoritative.\n\n"
            if len(exact - {None, ""}) > 1
            else ""
        )
        if None in exact or "" in exact:
            warning += "Some sources did not supply a license claim.\n\n"
        self.licenses.setPlainText(
            warning
            + "Source claims are unverified; they do not establish permission.\n\n"
            + "\n\n".join(claims)
        )
        others = [
            c for c in evidence["shared_candidates"] if c["candidate_id"] != value["candidate_id"]
        ]
        group = QTreeWidgetItem(
            ["Same image found elsewhere", f"{len(others)} other exact-byte histories"]
        )
        for other in others:
            for source in other.get("sources", []):
                group.addChild(
                    QTreeWidgetItem(
                        [
                            source["provider"],
                            f"{source.get('query') or 'No phrase'} · {source.get('license') or 'License not supplied'} · {source.get('source_page_url') or 'Source page not supplied'}",
                        ]
                    )
                )
        self.sources.addTopLevelItem(group)
        group.setExpanded(True)
        self.history.setPlainText(review_history_text(review, evidence["review_events"]))
        candidates = evidence["screening"]["candidates"]
        if candidates:
            result = candidates[0]
            labels = {
                "review_required": "Needs human review",
                "excluded": "Excluded by screening",
                "eligible": "Eligible under screening rules",
            }
            text = "Rule result: " + labels[result["rule_disposition"]] + "\n" + result["reason"]
            override = result.get("override")
            text += "\nInvestigator override: " + (
                labels[override["new_disposition"]] + " — " + override.get("note", "")
                if override
                else "None"
            )
            self.rule_summary.setText(text)
        self.observations.blockSignals(True)
        self.observations.clear()
        self.observations.addItem("No saved result selected", None)
        for obs in reversed(observations):
            self.observations.addItem(
                f"{obs['status'].replace('_', ' ')} · {obs['timestamp']}", obs
            )
        self.observations.setCurrentIndex(1 if self.observations.count() > 1 else 0)
        self.observations.blockSignals(False)
        self.observation_changed()
        show_json(self.evidence, evidence)

    def observation_changed(self):
        obs = self.observations.currentData()
        matching = bool(
            obs and self.current and obs["asset_id"] == self.current["evidence"]["asset_id"]
        )
        self.image.observation = obs if matching else None
        if obs:
            text = (
                screening_label([obs])
                + "\nSaved machine result; not a human review decision. Not detected does not establish absence. Rules and investigator overrides remain separate."
            )
            if not matching:
                text += "\nHistorical observation for another asset: overlay disabled."
        else:
            text = "No saved result selected. This viewer does not run screening."
        self.observation_summary.setText(text)
        self.image.update()

    def overlay_changed(self, checked):
        self.image.show_boxes = checked
        self.image.update()


class SortItem(QTableWidgetItem):
    def __lt__(self, other):
        return (self.data(Qt.ItemDataRole.UserRole + 1), self.data(Qt.ItemDataRole.UserRole)) < (
            other.data(Qt.ItemDataRole.UserRole + 1),
            other.data(Qt.ItemDataRole.UserRole),
        )


class CandidateBrowser(QWidget):
    inspect = Signal(str)
    cleared = Signal()
    marks_changed = Signal()

    def __init__(self, available_only=False, triage=False):
        super().__init__()
        self.setMinimumWidth(200)
        self.rows = {}
        self.populating = False
        self.root = None
        self.marked_ids = set()
        self.visible_ids = []
        self.page = 0
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.availability = QComboBox()
        self.availability.addItems(
            [
                "All results",
                "Available images",
                "Unavailable results",
                "Failed downloads",
                "Pending downloads",
            ]
        )
        self.availability.setCurrentIndex(1 if available_only else 0)
        self.availability.setAccessibleName("Image availability filter")
        top.addWidget(self.availability, 1)
        self.filters_toggle = QCheckBox("More filters")
        top.addWidget(self.filters_toggle)
        box.addLayout(top)
        self.facets = QWidget()
        form = QGridLayout(self.facets)
        form.setContentsMargins(0, 0, 0, 0)
        self.selectors = {}
        for i, (key, label) in enumerate(
            (
                ("provider", "Provider"),
                ("query", "Search phrase"),
                ("review", "Human review"),
                ("screening", "Screening"),
                ("license", "Exact license claim"),
                ("failure", "Failure reason"),
                ("shared", "Exact-byte histories"),
                ("disposition", "Effective screening disposition"),
            )
        ):
            combo = QComboBox()
            combo.setMinimumWidth(0)
            combo.setSizeAdjustPolicy(
                QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
            )
            combo.setAccessibleName(label + " filter")
            combo.addItem("All " + label.lower(), None)
            self.selectors[key] = combo
            form.addWidget(combo, i // 2, i % 2)
            combo.currentIndexChanged.connect(self.filter_rows)
        self.min_width, self.min_height = QSpinBox(), QSpinBox()
        for i, (spin, label) in enumerate(
            ((self.min_width, "Min width: "), (self.min_height, "Min height: "))
        ):
            spin.setRange(0, 100000)
            spin.setPrefix(label)
            spin.setAccessibleName(label + "pixels")
            spin.valueChanged.connect(self.filter_rows)
            form.addWidget(spin, 4, i)
        self.filter_dialog = QDialog(self)
        self.filter_dialog.setWindowTitle("Filter this view")
        filters_layout = QVBoxLayout(self.filter_dialog)
        filters_layout.addWidget(
            text_label(
                "Provider, phrase and license must match the same source record. These filters only change this view."
            )
        )
        filters_layout.addWidget(self.facets)
        button(filters_layout, "Clear additional filters", self.clear_filters)
        button(filters_layout, "Done", self.filter_dialog.accept)
        self.filter_dialog.finished.connect(lambda _: self.filters_toggle.setChecked(False))
        self.filters_toggle.toggled.connect(self.filter_dialog.setVisible)
        self.filter_dialog.resize(440, 300)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Find title or source text…")
        self.filter.setAccessibleName("Find images")
        box.addWidget(self.filter)
        self.counts = text_label()
        self.counts.setFixedHeight(20)
        self.counts.setWordWrap(False)
        box.addWidget(self.counts)
        controls = QHBoxLayout()
        self.view = QComboBox()
        self.view.addItems(["Table", "Thumbnails"])
        self.view.setAccessibleName("Image browsing view")
        controls.addWidget(self.view)
        self.order = QComboBox()
        for column, ascending, descending in (
            (8, "First discovered", "Last discovered"),
            (0, "Title A–Z", "Title Z–A"),
            (6, "Width ↑", "Width ↓"),
            (7, "Height ↑", "Height ↓"),
            (1, "Review A–Z", "Review Z–A"),
            (2, "Screening A–Z", "Screening Z–A"),
            (3, "Source A–Z", "Source Z–A"),
            (4, "License A–Z", "License Z–A"),
            (5, "Availability A–Z", "Availability Z–A"),
        ):
            self.order.addItem(ascending, column * 2)
            self.order.addItem(descending, column * 2 + 1)
        self.order.setAccessibleName("Sort image view and direction")
        self.order.setMinimumContentsLength(9)
        self.order.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        controls.addWidget(self.order)
        self.previous = button(controls, "‹", lambda: self.move(-1))
        self.previous.setAccessibleName("Previous image in filtered order (Alt+Left)")
        self.position = text_label("No image selected")
        controls.addWidget(self.position, 1)
        self.next = button(controls, "›", lambda: self.move(1))
        self.next.setAccessibleName("Next image in filtered order (Alt+Right)")
        box.addLayout(controls)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(
            [
                "Image",
                "Human review",
                "Screening",
                "Found through",
                "Exact license claims",
                "Availability",
                "Width",
                "Height",
                "First discovered",
            ]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(8, Qt.SortOrder.AscendingOrder)
        self.table.setColumnHidden(8, True)
        self.order.currentIndexChanged.connect(
            lambda: self.table.sortByColumn(
                self.order.currentData() // 2, Qt.SortOrder(self.order.currentData() % 2)
            )
        )
        self.table.setMinimumHeight(100)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for i, width in enumerate((175, 100, 130, 200, 170, 105, 65, 65)):
            self.table.setColumnWidth(i, width)
        self.stack = QStackedWidget()
        self.stack.setMinimumHeight(155)
        self.stack.addWidget(self.table)
        self.grid = ThumbnailGrid(self)
        self.grid.markable = triage
        self.stack.addWidget(self.grid)
        box.addWidget(self.stack, 1)
        self.page_bar = QWidget()
        paging = QHBoxLayout(self.page_bar)
        paging.setContentsMargins(0, 0, 0, 0)
        self.previous_page = button(paging, "Previous page", lambda: self.turn_page(-1))
        self.page_count = text_label()
        paging.addWidget(self.page_count, 1)
        self.next_page = button(paging, "Next page", lambda: self.turn_page(1))
        box.addWidget(self.page_bar)
        self.page_bar.hide()
        self.view.currentIndexChanged.connect(self.change_view)
        self.grid.chosen.connect(self.select_visible)
        self.grid.marked.connect(self.mark)
        self.table.itemSelectionChanged.connect(self.selected)
        self.filter.textChanged.connect(self.filter_rows)
        self.availability.currentIndexChanged.connect(self.filter_rows)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self.sort_changed)
        self.table.model().layoutChanged.connect(self.filter_rows)
        from PySide6.QtGui import QKeySequence, QShortcut

        for key, step in (("Alt+Left", -1), ("Alt+Right", 1)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda step=step: self.move(step))
        if triage:
            self.view.setCurrentIndex(1)

    def sort_changed(self, column, order):
        self.order.blockSignals(True)
        self.order.setCurrentIndex(self.order.findData(column * 2 + order.value))
        self.order.blockSignals(False)

    def change_view(self, index):
        self.stack.setCurrentIndex(index)
        self.page_bar.setVisible(bool(index) and len(self.visible_ids) > self.grid.page_size)
        self.refresh_grid()

    def refresh_grid(self):
        if self.view.currentIndex() != 1:
            return
        size = self.grid.page_size
        self.page_bar.setVisible(len(self.visible_ids) > size)
        self.page = min(self.page, max(0, (len(self.visible_ids) - 1) // size))
        ids = self.visible_ids[self.page * size : (self.page + 1) * size]
        self.grid.fill(
            [self.rows[cid] for cid in ids], self.candidate_id(), self.marked_ids, self.root
        )
        self.page_count.setText(
            f"Page {self.page + 1} / {max(1, (len(self.visible_ids) + size - 1) // size)}"
        )
        self.previous_page.setEnabled(self.page > 0)
        self.next_page.setEnabled((self.page + 1) * size < len(self.visible_ids))

    def turn_page(self, step):
        self.page = max(0, self.page + step)
        self.refresh_grid()

    def select_visible(self, cid):
        if cid not in self.visible_ids:
            return
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == cid:
                self.table.selectRow(i)
                self.table.scrollToItem(self.table.item(i, 0))
                return

    def move(self, step):
        cid = self.candidate_id()
        index = (
            self.visible_ids.index(cid)
            if cid in self.visible_ids
            else (-1 if step > 0 else len(self.visible_ids))
        )
        index += step
        if 0 <= index < len(self.visible_ids):
            self.select_visible(self.visible_ids[index])

    def mark(self, cid, checked):
        row = self.rows.get(cid, {})
        if (
            checked
            and cid in self.visible_ids
            and row.get("displayable", row.get("available", False))
        ):
            self.marked_ids.add(cid)
        else:
            self.marked_ids.discard(cid)
        self.marks_changed.emit()

    def clear_marks(self):
        self.marked_ids.clear()
        self.refresh_grid()
        self.marks_changed.emit()

    def clear_filters(self):
        self.filter.clear()
        for combo in self.selectors.values():
            combo.setCurrentIndex(0)
        self.min_width.setValue(0)
        self.min_height.setValue(0)

    def populate(self, candidates, root=None):
        self.populating = True
        prior = self.candidate_id()
        if root != self.root:
            self.marked_ids.clear()
            self.grid.cache.clear()
            self.page = 0
        self.root = root
        self.rows = {r["candidate_id"]: r for r in candidates}
        values = {k: set() for k in self.selectors}
        for c in candidates:
            for source in c["sources"]:
                for key in ("provider", "query", "license"):
                    values[key].add(source.get(key) or "Not supplied")
            values["review"].add(c.get("review_text", "Unreviewed"))
            values["screening"].add(c.get("screening_text", "Not run"))
            values["disposition"].add(disposition_text(c))
            if c.get("failure_kind"):
                values["failure"].add(c["failure_kind"])
            values["shared"].add(
                "Multiple histories"
                if c.get("shared_asset_candidates", 0) > 1
                else "Single history or no acquired asset"
            )
        for key, combo in self.selectors.items():
            selected = combo.currentData()
            label = combo.itemText(0)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(label, None)
            if selected is not None:
                values[key].add(
                    selected
                )  # Keep an active zero-match filter explicit after refresh.
            for value in sorted(values[key]):
                combo.addItem(FAILURES[value][0] if key == "failure" else value, value)
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.blockSignals(False)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(candidates))
        for i, c in enumerate(candidates):
            sources = c["sources"]
            texts = [
                c.get("title") or title_for(c),
                c.get("review_text", "Unreviewed"),
                c.get("screening_text", "Not run"),
                "; ".join(sorted({f"{s['provider']}: {s['query']}" for s in sources})),
                "; ".join(sorted({s.get("license") or "Not supplied" for s in sources})),
                c.get("availability", "Unavailable"),
                str(c.get("width") or "?"),
                str(c.get("height") or "?"),
                str(i + 1),
            ]
            for j, text in enumerate(texts):
                item = SortItem(text)
                item.setData(Qt.ItemDataRole.UserRole, c["candidate_id"])
                item.setData(
                    Qt.ItemDataRole.UserRole + 1,
                    i
                    if j == 8
                    else (c.get("width" if j == 6 else "height") or 0)
                    if j >= 6
                    else text.casefold(),
                )
                item.setToolTip(text)
                self.table.setItem(i, j, item)
        self.table.setSortingEnabled(True)
        if prior:
            for i in range(self.table.rowCount()):
                if self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == prior:
                    self.table.selectRow(i)
        self.table.blockSignals(False)
        self.populating = False
        self.filter_rows()

    def candidate_id(self):
        items = self.table.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def selected(self):
        cid = self.candidate_id()
        self.position.setText(
            f"{self.visible_ids.index(cid) + 1:,} / {len(self.visible_ids):,}"
            if cid in self.visible_ids
            else "No image selected"
        )
        if cid in self.visible_ids:
            self.page = self.visible_ids.index(cid) // self.grid.page_size
            # Sync selection without rebuilding the page for each arrow key.
            page_ids = [
                self.grid.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.grid.count())
            ]
            if self.view.currentIndex() == 1 and cid in page_ids:
                self.grid.blockSignals(True)
                self.grid.setCurrentRow(page_ids.index(cid))
                self.grid.blockSignals(False)
            else:
                self.refresh_grid()
        if cid:
            self.inspect.emit(cid)
        else:
            self.cleared.emit()

    def select_candidate(self, candidate_id):
        self.filter.clear()
        for combo in self.selectors.values():
            combo.setCurrentIndex(0)
        self.min_width.setValue(0)
        self.min_height.setValue(0)
        self.availability.setCurrentIndex(
            0
        )  # Explicit blocker navigation may reveal unavailable records.
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == candidate_id:
                self.table.selectRow(i)
                self.table.scrollToItem(self.table.item(i, 0))
                return

    def filter_rows(self, *args):
        if self.populating:
            return
        term = self.filter.text().casefold()
        facets = {k: v.currentData() for k, v in self.selectors.items()}
        visible = 0
        self.visible_ids = []
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 0)
            if item is None:
                continue
            c = self.rows[item.data(Qt.ItemDataRole.UserRole)]
            available = c.get("displayable", c.get("available", False))
            mode = self.availability.currentIndex()
            match = (
                mode == 0
                or mode == 1
                and available
                or mode == 2
                and not available
                or mode == 3
                and c.get("download_status") == "failed"
                or mode == 4
                and c.get("download_status") is None
            )
            match = match and (
                not term
                or any(
                    term in self.table.item(i, j).text().casefold()
                    for j in range(self.table.columnCount())
                )
            )
            # Provider/query/license constraints match one source relationship, not a fictitious combined claim.
            if any(facets[k] for k in ("provider", "query", "license")):
                match = match and any(
                    all(
                        not facets[k] or (s.get(k) or "Not supplied") == facets[k]
                        for k in ("provider", "query", "license")
                    )
                    for s in c["sources"]
                )
            for key, field in (
                ("review", "review_text"),
                ("screening", "screening_text"),
                ("failure", "failure_kind"),
            ):
                match = match and (not facets[key] or c.get(field) == facets[key])
            match = (
                match
                and (c.get("width") or 0) >= self.min_width.value()
                and (c.get("height") or 0) >= self.min_height.value()
            )
            shared = (
                "Multiple histories"
                if c.get("shared_asset_candidates", 0) > 1
                else "Single history or no acquired asset"
            )
            match = match and (not facets["shared"] or facets["shared"] == shared)
            match = match and (
                not facets["disposition"] or facets["disposition"] == disposition_text(c)
            )
            self.table.setRowHidden(i, not match)
            visible += bool(match)
            if match:
                self.visible_ids.append(c["candidate_id"])
            if not match and item.isSelected():
                self.table.clearSelection()
        active = (
            sum(bool(v) for v in facets.values())
            + bool(term)
            + bool(self.min_width.value())
            + bool(self.min_height.value())
        )
        self.filters_toggle.setText(f"More filters ({active})" if active else "More filters")
        labels = [v.currentText() for v in self.selectors.values() if v.currentData()]
        if term:
            labels.append(f"Text: {self.filter.text()}")
        if self.min_width.value() or self.min_height.value():
            labels.append(
                f"Minimum {self.min_width.value()} × {self.min_height.value()} px (unknown excluded)"
            )
        self.counts.setText(
            f"{visible:,} visible / {len(self.rows):,} total · "
            + (" · ".join(labels) if labels else self.availability.currentText())
        )
        self.counts.setToolTip(
            self.counts.text() + "\nView filters never change review or freeze membership."
        )
        self.filters_toggle.setToolTip("\n".join(labels) if labels else "No additional filters")
        self.filters_toggle.setAccessibleDescription(self.filters_toggle.toolTip())
        self.marked_ids.intersection_update(self.visible_ids)
        self.marks_changed.emit()
        cid = self.candidate_id()
        self.position.setText(
            f"{self.visible_ids.index(cid) + 1:,} / {visible:,}"
            if cid in self.visible_ids
            else "No image selected"
        )
        if cid in self.visible_ids:
            self.page = self.visible_ids.index(cid) // self.grid.page_size
        self.refresh_grid()


def candidate_split(browser, inspector):
    splitter = QSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(browser)
    splitter.addWidget(inspector)
    browser.cleared.connect(inspector.clear)
    splitter.setSizes([360, 470])
    return splitter
