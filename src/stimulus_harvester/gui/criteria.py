"""Researcher intent first; explicit interpretations before any policy activation."""

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stimulus_harvester.screening import capabilities
from stimulus_harvester.screening.config import Criterion, ScreeningConfig
from stimulus_harvester.storage import new_id

from .components import button, text_label


class CriterionDialog(QDialog):
    def __init__(self, parent, value=None):
        super().__init__(parent)
        self.setWindowTitle("Criterion interpretation")
        self.resize(570, 540)
        self.original = value
        box = QVBoxLayout(self)
        form = QFormLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText(
            "people, non-human animals, cars, televisions, or your own criterion"
        )
        self.text.setAccessibleName("Exclude images containing")
        form.addRow("Exclude images containing", self.text)
        box.addLayout(form)
        self.explanation = text_label()
        box.addWidget(self.explanation)
        self.manual = QCheckBox("Retain as a manual-only criterion")
        box.addWidget(self.manual)
        box.addWidget(
            text_label("Exact observable targets (select to edit the proposed interpretation):")
        )
        self.targets = QListWidget()
        self.targets.setAccessibleName("Exact supported observable targets")
        self.targets.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for name, class_id in capabilities.labels().items():
            item = QListWidgetItem(f"{name} (class {class_id})")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.targets.addItem(item)
        box.addWidget(self.targets, 1)
        self.confirmed = QCheckBox(
            "I confirm this interpretation and its stated coverage limitations"
        )
        box.addWidget(self.confirmed)
        self.error = text_label()
        box.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept_valid)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.text.textChanged.connect(self.propose)
        self.manual.toggled.connect(lambda manual: self.targets.setEnabled(not manual))
        self.targets.itemSelectionChanged.connect(lambda: self.confirmed.setChecked(False))
        self.manual.toggled.connect(lambda: self.confirmed.setChecked(False))
        if value:
            self.text.setText(value["text"])
            self.manual.setChecked(value["mode"] == "manual")
            self.select_targets(value["targets"])
        else:
            self.propose()

    def select_targets(self, targets):
        for i in range(self.targets.count()):
            item = self.targets.item(i)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in targets)

    def propose(self, *_):
        proposal = capabilities.propose(self.text.text())
        self.explanation.setText(proposal["explanation"])
        self.select_targets(proposal["targets"])
        self.confirmed.setChecked(False)

    def value(self):
        proposal = capabilities.propose(self.text.text())
        targets = (
            []
            if self.manual.isChecked()
            else [i.data(Qt.ItemDataRole.UserRole) for i in self.targets.selectedItems()]
        )
        mapping = (
            proposal["mapping_id"] if targets == proposal["targets"] else "custom:confirmed-labels"
        )
        data = dict(
            id=self.original["id"] if self.original else new_id("criterion"),
            text=self.text.text().strip(),
            mode="manual" if self.manual.isChecked() else "automatic",
            targets=targets,
            coverage="manual"
            if self.manual.isChecked()
            else "partial"
            if proposal["support"] in {"partial", "unsupported"} or targets != proposal["targets"]
            else "exact",
            mapping_id=mapping,
            registry_version=proposal["registry_version"],
            limitations=proposal["explanation"]
            + (
                " Only the explicitly selected labels can be observed; inspect the remaining intent manually."
                if mapping == "custom:confirmed-labels"
                else ""
            ),
            confirmed=self.confirmed.isChecked(),
        )
        return Criterion.model_validate(data).model_dump(mode="json")

    def accept_valid(self):
        try:
            self.value()
        except ValueError:
            self.error.setText(
                "Enter a criterion, choose explicit targets or manual-only, and confirm the interpretation."
            )
            return
        self.accept()


class CriteriaPanel(QWidget):
    def __init__(self, screen):
        super().__init__()
        self.screen, self.window, self.configuration = screen, screen.window, {}
        box = QVBoxLayout(self)
        box.addWidget(
            text_label(
                "Exclude images containing…\nRecord your intent first. Automatic checks require an explicitly configured mechanism and thresholds."
            )
        )
        self.items = QTreeWidget()
        self.items.setHeaderLabels(
            ["Criterion", "Operational targets", "Automatic mechanism / review"]
        )
        self.items.setAccessibleName("Screening criteria for selected scope")
        self.items.setColumnWidth(0, 190)
        self.items.setColumnWidth(1, 230)
        box.addWidget(self.items, 1)
        row = QHBoxLayout()
        button(row, "Add criterion…", self.add)
        button(row, "Inspect / edit…", self.edit)
        button(row, "Remove criterion", self.remove)
        box.addLayout(row)
        row = QHBoxLayout()
        button(row, "Automatic settings…", lambda: screen.views.setCurrentIndex(1))
        button(row, "Evaluate saved observations", self.evaluate)
        self.run = button(row, "Run screening…", screen.run_screening)
        box.addLayout(row)
        self.summary = text_label(
            "No criteria configured. No detector or thresholds selected by default."
        )
        box.addWidget(self.summary)
        box.addWidget(
            text_label(
                "Combination: any detected target excludes; uncertain, no-match and incomplete checks need review. Manual/partial coverage requires an attributed screening override after inspection; Accept/Reject remains a separate suitability decision."
            )
        )

    def populate(self, config, report):
        self.configuration = copy.deepcopy(config or {})
        self.items.clear()
        cfg = ScreeningConfig.model_validate(self.configuration)
        for criterion in cfg.criteria:
            state = capabilities.capability(
                criterion,
                cfg.detectors,
                installed=self.window.dependencies.get("person_detector", {}).get(
                    "available", False
                ),
            )
            item = QTreeWidgetItem(
                [criterion.text, ", ".join(criterion.targets) or "Human inspection", state]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, criterion.model_dump(mode="json"))
            for column in range(3):
                item.setToolTip(column, item.text(column) + "\n" + criterion.limitations)
            self.items.addTopLevelItem(item)
        self.summary.setText(
            f"{len(cfg.criteria)} criteria in this scope. Review image-level reasons under Saved observations & images."
            if cfg.criteria_version
            else "Legacy person policy remains active."
            if cfg.detectors
            else "No criteria configured. Automatic screening not configured."
        )
        self.run.setEnabled(
            bool(
                cfg.criteria_version
                and cfg.detectors
                and any(d.enabled for d in cfg.detectors)
                and self.window.dependencies.get("person_detector", {}).get("available", False)
            )
        )

    def configuration_with(self, criteria):
        cfg = copy.deepcopy(self.configuration)
        if not cfg.get("criteria_version") and len(cfg.get("detectors", [])) > 1:
            raise ValueError(
                "Choose one explicit automatic mechanism before replacing a multi-detector legacy policy."
            )
        for detector in cfg.get("detectors", []):
            detector["property"] = "coco_objects"
        cfg.update(criteria_version=1, criteria=criteria, rules=[])
        return ScreeningConfig.model_validate(cfg).model_dump(mode="json")

    def save(self, criteria):
        try:
            configuration = self.configuration_with(criteria)
        except ValueError as exc:
            self.window.notice(str(exc))
            return
        if self.configuration.get("rules") and not self.window.confirm(
            "Activate generalized criteria?",
            "This replaces the live legacy person policy with these confirmed criteria. Historical evidence remains intact; legacy person observations will not be reinterpreted as object evidence. Existing explicitly chosen mechanism settings are retained.",
        ):
            return
        self.window.submit(
            "criteria-policy",
            dict(configuration=configuration, actor=self.window.review.reviewer.text()),
        )

    def add(self):
        dialog = CriterionDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save(self.configuration.get("criteria", []) + [dialog.value()])

    def edit(self):
        item = self.items.currentItem()
        if item:
            old = item.data(0, Qt.ItemDataRole.UserRole)
            dialog = CriterionDialog(self, old)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.save(
                    [
                        dialog.value() if c["id"] == old["id"] else c
                        for c in self.configuration["criteria"]
                    ]
                )

    def remove(self):
        item = self.items.currentItem()
        if item and self.window.confirm(
            "Remove criterion from live policy?",
            "Earlier criteria, evaluations and frozen evidence remain preserved.",
        ):
            old = item.data(0, Qt.ItemDataRole.UserRole)
            self.save([c for c in self.configuration["criteria"] if c["id"] != old["id"]])

    def evaluate(self):
        if self.configuration.get("criteria_version"):
            self.window.submit(
                "criteria-policy",
                dict(configuration=self.configuration, actor=self.window.review.reviewer.text()),
            )
        else:
            self.window.notice(
                "Add a criterion first, or use the retained legacy policy controls in Automatic settings."
            )
