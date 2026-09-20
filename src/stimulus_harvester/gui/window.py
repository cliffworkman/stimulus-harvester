"""Persistent project/session shell for the six-stage desktop workflow."""

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QScrollArea,
    QProgressBar,
    QCheckBox,
    QMessageBox,
    QApplication,
)

from .components import text_label, button, details, show_json
from .pages import ProjectPage, DiscoverPage, ScreenPage, ReviewPage, ArtifactPage
from .tasks import Session


class Window(QMainWindow):
    def __init__(self, *, adapter=None):
        super().__init__()
        self.setWindowTitle("Stimulus Harvester — Research stimulus discovery & adjudication")
        self.resize(1260, 940)
        self.setMinimumSize(820, 650)
        self.session = Session(self, adapter)
        self.system_palette = QApplication.instance().palette()
        self.system_style = QApplication.instance().style().objectName()
        self.dependencies = {}
        self.inspect_target = None
        self.artifact_target = None
        self.pending_freeze_request = None
        self.followup = None
        self.active_action = None
        central = QWidget()
        self.setCentralWidget(central)
        box = QVBoxLayout(central)
        heading = text_label("Stimulus Harvester")
        heading.setStyleSheet("font-size:24px;font-weight:600")
        box.addWidget(heading)
        box.addWidget(
            text_label("Research stimulus discovery & adjudication · Stimulus Modernization Suite")
        )
        self.context = text_label("No project open — start with Project.")
        self.warning = text_label("No production person-detector default is selected.")
        box.addWidget(self.context)
        box.addWidget(self.warning)
        self.tabs = QTabWidget()
        box.addWidget(self.tabs, 1)
        self.project = ProjectPage(self)
        self.discover = DiscoverPage(self)
        self.screen = ScreenPage(self)
        self.review = ReviewPage(self)
        self.assemble = ArtifactPage(self)
        self.export = ArtifactPage(self, exporting=True)
        self.pages = [
            self.project,
            self.discover,
            self.screen,
            self.review,
            self.assemble,
            self.export,
        ]
        for title, page in zip(
            (
                "1  Project",
                "2  Discover",
                "3  Screen",
                "4  Review freeze",
                "5  Assemble",
                "6  Export",
            ),
            self.pages,
        ):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self.tabs.addTab(scroll, title)
        for page in self.pages[1:]:
            page.setEnabled(False)
        self.operation = text_label("Ready")
        self.operation.setAccessibleName("Current operation status")
        bottom = QHBoxLayout()
        bottom.addWidget(self.operation, 1)
        self.cancel = button(bottom, "Cancel before start", self.cancel_requested)
        self.cancel.setEnabled(False)
        self.cancel.setToolTip(
            "Only cancels before backend invocation. Running stages cannot be interrupted safely; the window remains open until they finish."
        )
        box.addLayout(bottom)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        box.addWidget(self.progress)
        self.details_toggle = QCheckBox("Operation details / diagnostics")
        box.addWidget(self.details_toggle)
        self.log = details()
        self.log.setMaximumHeight(180)
        self.log.hide()
        self.details_toggle.toggled.connect(self.log.setVisible)
        box.addWidget(self.log)
        self.setStyleSheet(
            "QWidget{font-size:13px} QPushButton{padding:6px 10px} QLineEdit,QComboBox,QSpinBox{padding:4px} QTabBar::tab{padding:9px 12px} QGroupBox{margin-top:9px;padding-top:12px}"
        )
        self.session.changed.connect(self.populate)
        self.session.busy_changed.connect(self.busy_changed)
        self.session.progress.connect(self.progress_event)
        self.session.completed.connect(self.completed)
        QTimer.singleShot(0, self.initial_dependencies)

    def initial_dependencies(self):
        if not self.session.busy:
            self.submit("dependencies")

    def submit(self, action, payload=None, *, path=None):
        if self.session.busy:
            self.notice("An operation is running. It must finish before another begins.")
            return False
        if action not in {"create", "open", "demo", "dependencies"} and not self.session.state:
            self.notice("Open or create a project first.")
            return False
        self.active_action = action
        self.operation.setText(
            f"Queued: {action}. Cancellation is available only before the backend starts."
        )
        return self.session.submit(action, payload, path=path)

    def configure_then(self, action, configuration):
        self.followup = (action, {})
        self.submit("configure", {"configuration": configuration})

    def inspect(self, target, candidate_id):
        if self.session.busy:
            return
        self.inspect_target = target
        self.submit("inspect", {"candidate_id": candidate_id})

    def inspect_artifact(self, target, artifact_id):
        self.artifact_target = target
        self.submit("artifact", {"artifact_id": artifact_id})

    def navigate_candidate(self, candidate_id):
        self.tabs.setCurrentIndex(3)
        self.review.modes.setCurrentIndex(0)
        self.review.browser.select_candidate(candidate_id)
        if not self.session.busy:
            self.inspect(self.review.inspector, candidate_id)

    def notice(self, text):
        self.operation.setText(text)

    def confirm(self, title, message):
        return (
            QMessageBox.question(
                self,
                title,
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        )

    def set_appearance(self, mode):
        from .appearance import apply_appearance

        apply_appearance(self, mode)

    @Slot(object)
    def populate(self, state):
        previous = getattr(self, "displayed_project", None)
        if previous != state.project_id:
            for inspector in (
                self.discover.inspector,
                self.screen.inspector,
                self.review.inspector,
            ):
                inspector.current = None
                inspector.identity.setText(
                    "Select a candidate in this project to inspect its evidence."
                )
                inspector.observations.clear()
                inspector.image.set_image(b"")
                inspector.evidence.clear()
                inspector.sources.clear()
            for page in (self.assemble, self.export):
                page.current_artifact = None
                page.manifest.clear()
            self.review.notes.clear()
        self.displayed_project = state.project_id
        self.context.setText(
            f"Current project: {state.name} · {state.path}\n{state.project_id} · {state.status['counts']['candidates']} candidates · decisions {state.status['reviews']} · {len(state.artifacts)} immutable artifacts"
        )
        self.warning.setText(
            "\n".join(state.warnings)
            or "Current project is mutable. Frozen artifacts are read-only. No default detector is selected."
        )
        for page in self.pages:
            page.populate(state)

    @Slot(bool)
    def busy_changed(self, busy):
        for i, page in enumerate(self.pages):
            page.setEnabled(not busy and (i == 0 or self.session.state is not None))
        self.cancel.setEnabled(busy)
        self.progress.setRange(0, 0 if busy else 1)
        if not busy:
            self.progress.setValue(1)
            self.screen.refresh_capability()
            if self.session.state and self.session.state.config is None:
                self.discover.setEnabled(False)
                self.screen.setEnabled(False)

    @Slot(object)
    def progress_event(self, event):
        status = event.get("status", "")
        self.operation.setText(
            f"{event.get('stage', self.active_action)}: {status}"
            + (
                " — running stage cannot be interrupted; progress is stage-level."
                if status == "started"
                else ""
            )
        )
        if status == "started":
            self.cancel.setEnabled(False)

    def cancel_requested(self):
        self.session.cancel_before_start()
        self.notice(
            "Cancellation requested before start. If the stage has already started, it will finish normally."
        )
        self.cancel.setEnabled(False)

    @Slot(object)
    def completed(self, reply):
        if reply.issue:
            self.followup = None
            self.notice(f"{reply.issue.category}: {reply.issue.message}\n{reply.issue.guidance}")
            self.log.setPlainText(reply.issue.details)
            self.details_toggle.setChecked(True)
            if reply.action == "search":
                self.discover.discovery_summary.setText(
                    reply.issue.guidance + "\n" + reply.issue.message
                )
                if isinstance(reply.value, dict):
                    self.discover.report("search", reply.value, "failed")
            return
        if reply.status == "cancelled":
            self.followup = None
            self.notice("Cancelled before the stage started. No running operation was hidden.")
            return
        self.notice(
            f"{reply.action}: {'Partial discovery — inspect provider/query failures' if reply.status == 'partial' else 'completed'}"
        )
        if reply.action == "dependencies":
            self.dependencies = reply.value
            self.project.capabilities.setText(
                " · ".join(f"{k}: {v['state']}" for k, v in reply.value.items())
            )
            self.screen.refresh_capability()
        elif reply.action in {"create", "demo", "open"} and not self.dependencies:
            self.submit("dependencies")
        elif reply.action == "inspect":
            if self.inspect_target:
                self.inspect_target.display(reply.value)
                if self.inspect_target is self.review.inspector:
                    self.review.notes.setText(reply.value["evidence"]["review"]["notes"] or "")
        elif reply.action == "validate":
            self.review.freeze.receive_validation(reply.value, self.pending_freeze_request)
            if not reply.value["valid"]:
                self.notice(
                    f"Freeze blocked: {len(reply.value['blockers'])} blockers. Double-click a candidate issue in Prepare freeze."
                )
        elif reply.action == "artifact":
            if self.artifact_target:
                self.artifact_target.display(reply.value)
        elif reply.action in {"review", "screen-override"}:
            if self.review.inspector.current:
                self.inspect(self.review.inspector, self.review.inspector.current["candidate_id"])
        elif reply.action == "configure" and self.followup:
            action, payload = self.followup
            self.followup = None
            self.submit(action, payload)
        elif reply.action in {"freeze-create", "assemble", "export-frozen"}:
            result = reply.value
            self.notice(
                f"Created {result['artifact_id']}\n{result['path']}\nManifest SHA-256: {result['manifest_sha256']}"
            )
            if reply.action == "freeze-create":
                self.review.freeze.summary.setText(
                    self.operation.text()
                    + "\nRead-only evidence is available in Assemble / Export history."
                )
                for page in (self.assemble, self.export):
                    page.freeze.setCurrentIndex(page.freeze.findData(result["artifact_id"]))
            target = self.export if reply.action == "export-frozen" else self.assemble
            self.inspect_artifact(target, result["artifact_id"])
        if reply.action not in {"inspect", "artifact"}:
            show_json(self.log, reply.value)
        if reply.action in {"preview_queries", "diagnostics", "search"}:
            self.discover.report(reply.action, reply.value, reply.status)
            self.details_toggle.setChecked(True)

    def closeEvent(self, event):
        if self.session.busy:
            self.notice(
                "Work is still running. Wait for completion before closing; no worker will be terminated."
            )
            event.ignore()
        else:
            event.accept()
