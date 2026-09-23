"""Compact collection context shared by search, curation, screening and freeze."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .components import text_label


class CollectionBar(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.groups = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(text_label("Collection"))
        self.selector = QComboBox()
        self.selector.setMinimumWidth(180)
        self.selector.addItem("All results · project review", None)
        layout.addWidget(self.selector, 1)
        self.actor = QLineEdit()
        self.actor.setPlaceholderText("Investigator")
        self.actor.setMaximumWidth(150)
        layout.addWidget(self.actor)
        self.manage = QComboBox()
        self.manage.addItems(
            [
                "Collections…",
                "Create",
                "Edit name / phrases",
                "Archive",
                "Restore",
                "Inspect history",
                "Add selected images to…",
                "Remove selected images",
            ]
        )
        layout.addWidget(self.manage)
        self.selector.currentIndexChanged.connect(self.changed)
        self.manage.activated.connect(self.manage_action)

    @property
    def collection_id(self):
        return self.selector.currentData()

    def populate(self, state):
        self.groups = state.collections
        self.selector.blockSignals(True)
        self.selector.clear()
        self.selector.addItem("All results · project review", None)
        for c in self.groups:
            self.selector.addItem(
                f"{c['name']}{' (archived)' if c['archived'] else ''} · {c['reviewed']}/{c['found']} reviewed",
                c["collection_id"],
            )
        self.selector.setCurrentIndex(max(0, self.selector.findData(state.collection_id)))
        self.selector.blockSignals(False)

    def changed(self):
        if not self.window.session.state or self.window.session.busy:
            if self.window.session.state:
                self.selector.blockSignals(True)
                self.selector.setCurrentIndex(
                    max(0, self.selector.findData(self.window.session.state.collection_id))
                )
                self.selector.blockSignals(False)
            return
        for page in (self.window.discover, self.window.screen, self.window.review):
            page.browser.clear_marks()
            page.inspector.clear()
        self.window.requested_inspection = None
        self.window.review.notes.clear()
        self.window.review.freeze.invalidate()
        self.window.submit("refresh")

    def manage_action(self, index):
        self.manage.setCurrentIndex(0)
        if index in {1, 2}:
            self.edit(new=index == 1)
        elif index in {3, 4}:
            if self.collection_id and self.window.confirm(
                "Change collection availability?",
                "Archive hides an active collection from ordinary work. Restore keeps its existing membership and review history. No evidence is deleted.",
            ):
                self.window.submit(
                    "collection",
                    {
                        "action": "archive" if index == 3 else "restore",
                        "collection_id": self.collection_id,
                        "actor": self.window.review.reviewer.text(),
                    },
                )
        elif index == 5 and self.collection_id:
            self.window.submit("collection-inspect", {"collection_id": self.collection_id})
        elif index in {6, 7}:
            self.change_membership(remove=index == 7)

    def edit(self, *, new):
        current = next((c for c in self.groups if c["collection_id"] == self.collection_id), None)
        if not new and current is None:
            self.window.notice("Select a collection to edit.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Create collection" if new else "Edit collection")
        box = QVBoxLayout(dialog)
        form = QFormLayout()
        name = QLineEdit("" if new else current["name"])
        phrases = QPlainTextEdit("" if new else "\n".join(p["text"] for p in current["phrases"]))
        phrases.setPlaceholderText("One search phrase per line")
        actor = QLineEdit(self.window.review.reviewer.text())
        form.addRow("Name", name)
        form.addRow("Search phrases", phrases)
        form.addRow("Investigator", actor)
        box.addLayout(form)
        box.addWidget(
            text_label(
                "Changes affect future searches. Previous discovery and review history stay intact."
            )
        )
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.window.review.reviewer.setText(actor.text())
            self.window.submit(
                "collection",
                {
                    "action": "create" if new else "edit",
                    "collection_id": None if new else self.collection_id,
                    "name": name.text(),
                    "phrases": [s.strip() for s in phrases.toPlainText().splitlines() if s.strip()],
                    "actor": actor.text(),
                    "expected_revision": None if new else current["event_id"],
                },
            )

    def change_membership(self, remove):
        pages = {1: self.window.discover, 2: self.window.screen, 3: self.window.review}
        page = pages.get(self.window.tabs.currentIndex(), self.window.review)
        browser = page.browser
        ids = sorted(browser.marked_ids) or (
            [browser.candidate_id()] if browser.candidate_id() else []
        )
        if not ids:
            self.window.notice("Select an image, or mark images in Review, first.")
            return
        destination = self.collection_id
        if not remove:
            choices = [c for c in self.groups if not c["archived"]]
            labels = [f"{c['name']} ({c['collection_id'][-6:]})" for c in choices]
            if not choices:
                self.window.notice("Create a collection first.")
                return
            chosen, ok = QInputDialog.getItem(
                self, "Add images to collection", "Destination", labels, 0, False
            )
            if not ok:
                return
            destination = choices[labels.index(chosen)]["collection_id"]
        if not destination:
            self.window.notice("Select the collection to remove images from.")
            return
        name = next(c["name"] for c in self.groups if c["collection_id"] == destination)
        if self.window.confirm(
            "Confirm collection membership",
            f"{'Remove' if remove else 'Add'} {len(ids)} explicitly selected candidate(s) {'from' if remove else 'to'} {name}?\nOther collections, source history and original bytes remain intact.",
        ):
            scopes = {
                c["candidate_id"]: m["scope"]
                for c in self.window.session.state.candidates
                for m in c["collections"]
                if m["collection_id"] == destination
            }
            self.window.submit(
                "collection",
                {
                    "action": "remove" if remove else "add",
                    "collection_id": destination,
                    "candidate_ids": ids,
                    "actor": self.window.review.reviewer.text(),
                    "reason": "Explicit image selection in curation",
                    **({"expected_scopes": [scopes[cid] for cid in ids]} if remove else {}),
                },
            )
