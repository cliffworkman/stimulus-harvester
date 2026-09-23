"""Persistent project/session shell for the six-stage desktop workflow."""

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from stimulus_harvester.curation import summary_text

from .components import button, details, show_json, text_label
from .pages import ArtifactPage, DiscoverPage, ProjectPage, ReviewPage, ScreenPage
from .tasks import Session


class Window(QMainWindow):
    def __init__(self, *, adapter=None):
        super().__init__()
        self.setWindowTitle("Stimulus Harvester — Research stimulus discovery & adjudication")
        self.resize(1260, 940)
        self.setMinimumSize(820, 650)
        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("&User guide", self.show_guide)
        help_menu.addAction("&About Stimulus Harvester", self.show_about)
        self.session = Session(self, adapter)
        self.system_palette = QApplication.instance().palette()
        self.system_style = QApplication.instance().style().objectName()
        self.dependencies = {}
        self.inspect_target = None
        self.requested_inspection = None
        self.running_inspection = None
        self.artifact_target = None
        self.pending_freeze_request = None
        self.followup = None
        self.active_action = None
        central = QWidget()
        self.setCentralWidget(central)
        box = QVBoxLayout(central)
        self.context = text_label("No project open — start with Project.")
        self.warning = text_label("No production person-detector default is selected.")
        self.context.setMaximumHeight(38)
        self.warning.setMaximumHeight(30)
        box.addWidget(self.context)
        box.addWidget(self.warning)
        from .collections import CollectionBar

        self.collections = CollectionBar(self)
        box.addWidget(self.collections)
        self.tabs = QTabWidget()
        box.addWidget(self.tabs, 1)
        self.project = ProjectPage(self)
        self.discover = DiscoverPage(self)
        self.screen = ScreenPage(self)
        self.review = ReviewPage(self)
        self.collections.actor.textChanged.connect(self.review.reviewer.setText)
        self.review.reviewer.textChanged.connect(self.collections.actor.setText)
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
        for page in (self.discover, self.screen, self.review):
            page.browser.cleared.connect(lambda page=page: self.clear_inspection(page.inspector))
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
            if page in (self.discover, self.screen, self.review):
                # Each long settings tab scrolls internally; the image workspace
                # receives the viewport height so common actions remain visible.
                self.tabs.addTab(page, title)
                continue
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
        self.progress.setMaximumHeight(12)
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
        self.copy_diagnostics = button(
            box,
            "Copy diagnostic report",
            lambda: QApplication.clipboard().setText(self.log.toPlainText()),
        )
        self.copy_diagnostics.hide()
        self.details_toggle.toggled.connect(self.copy_diagnostics.setVisible)
        self.setStyleSheet(
            "QWidget{font-size:13px} QPushButton{padding:6px 10px} QLineEdit,QComboBox,QSpinBox{padding:4px} QTabBar::tab{padding:6px 8px} QGroupBox{margin-top:9px;padding-top:12px}"
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
        payload = dict(payload or {})
        cid = self.collections.collection_id
        if action in {"create", "open", "demo"}:
            cid = None
        if cid and action != "dependencies":
            payload["view_collection_id"] = cid
            if action in {"search", "screen", "screen-policy"}:
                payload["collection_id"] = cid
                if action != "screen":
                    payload["actor"] = self.review.reviewer.text()
                if action == "search":
                    payload["expected_revision"] = self.session.state.status["collection"][
                        "event_id"
                    ]
            if action in {"download", "retry-downloads"}:
                payload["collection_id"] = cid
            if action in {"review", "review-batch"}:
                if action == "review":
                    r = self.review.inspector.current["evidence"]["review"]
                    selection = [
                        {
                            "candidate_id": r["scope"]["candidate_id"],
                            "scope": r["scope"],
                            "event_id": r["event_id"],
                        }
                    ]
                else:
                    selection = payload["selection"]
                payload = {
                    "view_collection_id": cid,
                    "action": "review",
                    "collection_id": cid,
                    "actor": payload["reviewer_email"],
                    "label": payload["label"],
                    "notes": payload.get("notes"),
                    "selection": selection,
                    "batch": action == "review-batch",
                }
                action = "collection"
            elif action == "screen-override":
                payload["action"] = "override"
                payload["collection_id"] = cid
                payload["actor"] = payload.pop("reviewer")
                action = "collection"
        self.active_action = action
        self.operation.setText(
            f"Queued: {action}. Cancellation is available only before the backend starts."
        )
        return self.session.submit(action, payload, path=path)

    def configure_then(self, action, configuration, *, payload=None):
        if self.session.busy:
            self.notice("Wait for the current operation before saving configuration.")
            return
        self.followup = (action, payload or {})
        self.submit("configure", {"configuration": configuration})

    def inspect(self, target, candidate_id):
        if self.session.busy and self.active_action != "inspect":
            return
        target.clear()
        target.identity.setText("Loading selected image…")
        self.requested_inspection = (target, candidate_id)
        if self.session.busy:
            return  # Coalesce rapid movement to the latest selection, never an unbounded queue.
        self.inspect_target = target
        self.running_inspection = (target, candidate_id)
        self.submit("inspect", {"candidate_id": candidate_id})

    def clear_inspection(self, target):
        if self.requested_inspection and self.requested_inspection[0] is target:
            self.requested_inspection = None

    def finish_inspection(self, reply):
        requested = self.requested_inspection
        if requested and requested == self.running_inspection:
            target, cid = requested
            browser = next(
                p.browser
                for p in (self.discover, self.screen, self.review)
                if p.inspector is target
            )
            if browser.candidate_id() == cid:
                if reply.issue:
                    target.identity.setText("Preview unavailable. " + reply.issue.message)
                elif reply.status != "cancelled":
                    target.display(reply.value)
                    if target is self.review.inspector:
                        self.review.notes.setText(reply.value["evidence"]["review"]["notes"] or "")
            self.requested_inspection = None
            self.notice("Image ready" if not reply.issue else "Image preview needs attention")
        elif requested:
            self.inspect(*requested)

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
                inspector.clear()
            for page in (self.assemble, self.export):
                page.current_artifact = None
                page.manifest.clear()
            self.review.notes.clear()
        self.displayed_project = state.project_id
        self.collections.populate(state)
        full_context = state.name + "\n" + summary_text(state.status["curation"]["summary"])
        self.context.setText("\n".join(full_context.splitlines()[:2]))
        if state.collection_id:
            self.context.setText(
                state.status["collection"]["name"]
                + " · collection review\n"
                + summary_text(state.status["curation"]["summary"]).splitlines()[0]
            )
        self.context.setToolTip(full_context)
        self.warning.setText(
            "\n".join(state.warnings)
            or "Current project is mutable. Frozen artifacts are read-only. Screening is optional; no default detector is selected."
        )
        self.warning.setToolTip(self.warning.text())
        for page in self.pages:
            page.populate(state)

    @Slot(bool)
    def busy_changed(self, busy):
        self.collections.setEnabled(not busy and self.session.state is not None)
        viewing = busy and self.active_action == "inspect"
        for i, page in enumerate(self.pages):
            page.setEnabled((not busy or viewing) and (i == 0 or self.session.state is not None))
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
        if reply.action == "inspect":
            self.finish_inspection(reply)
            return
        if reply.issue:
            self.followup = None
            if (
                reply.action in {"search", "download", "retry-downloads"}
                and reply.value is not None
            ):
                self.log.setPlainText(reply.issue.details)
                self.discover.report(reply.action, reply.value, reply.status)
                self.notice(
                    "Search needs attention; see the provider explanation."
                    if reply.action == "search"
                    else "Downloads need attention. See Coverage & failures for causes and eligible retries."
                )
                return
            self.notice(f"{reply.issue.category}: {reply.issue.message}\n{reply.issue.guidance}")
            self.log.setPlainText(reply.issue.details)
            self.details_toggle.setChecked(False)
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
            f"{reply.action}: "
            + (
                "Some downloads failed — see Coverage & failures for next steps"
                if reply.status == "partial" and reply.action in {"download", "retry-downloads"}
                else "Partial discovery — inspect provider/query failures"
                if reply.status == "partial"
                else "completed"
            )
        )
        if reply.action == "dependencies":
            self.dependencies = reply.value
            self.project.capabilities.setText(
                " · ".join(f"{k}: {v['state']}" for k, v in reply.value.items())
            )
            self.screen.refresh_capability()
        elif reply.action in {"create", "demo", "open"} and not self.dependencies:
            self.submit("dependencies")
        elif reply.action == "validate":
            self.review.freeze.receive_validation(reply.value, self.pending_freeze_request)
            if not reply.value["valid"]:
                self.notice(
                    f"Freeze blocked: {len(reply.value['blockers'])} blockers. Double-click a candidate issue in Prepare freeze."
                )
        elif reply.action == "artifact":
            if self.artifact_target:
                self.artifact_target.display(reply.value)
        elif reply.action in {"review", "review-batch", "screen-override", "collection"}:
            if (
                reply.action == "collection"
                and isinstance(reply.value.get("result"), dict)
                and reply.value["result"].get("action") == "create"
            ):
                show_json(self.log, reply.value)
                self.collections.selector.setCurrentIndex(
                    self.collections.selector.findData(reply.value["collection_id"])
                )
                return
            if reply.action in {"review-batch", "collection"}:
                self.review.browser.clear_marks()
            next_id, self.review.next_after_review = self.review.next_after_review, None
            if reply.action == "review" and next_id in self.review.browser.visible_ids:
                self.review.browser.select_visible(next_id)
            elif self.review.browser.candidate_id():
                self.inspect(self.review.inspector, self.review.browser.candidate_id())
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
        if reply.action == "collection-inspect":
            self.details_toggle.setChecked(True)
        if reply.action in {
            "preview_queries",
            "diagnostics",
            "search",
            "download",
            "retry-downloads",
        }:
            self.discover.report(reply.action, reply.value, reply.status)
            if reply.action in {"preview_queries", "diagnostics"}:
                self.details_toggle.setChecked(False)

    def show_about(self):
        from stimulus_harvester import __version__

        QMessageBox.about(
            self,
            "About Stimulus Harvester",
            f"Stimulus Harvester {__version__}\nResearch stimulus discovery & adjudication\n"
            "MIT · Stimulus Harvester contributors\nNo production detector default.\n"
            "Third-party license inventory accompanies the distribution.",
        )

    def show_guide(self):
        from importlib.resources import files

        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextBrowser

        dialog = QDialog(self)
        dialog.setWindowTitle("Stimulus Harvester user guide")
        dialog.resize(760, 650)
        box = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setMarkdown(
            files("stimulus_harvester")
            .joinpath("resources/USER-GUIDE.md")
            .read_text(encoding="utf-8")
        )
        box.addWidget(browser)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        box.addWidget(buttons)
        dialog.exec()

    def closeEvent(self, event):
        if self.session.busy:
            self.notice(
                "Work is still running. Wait for completion before closing; no worker will be terminated."
            )
            event.ignore()
        else:
            for page in (self.discover, self.screen, self.review):
                page.browser.grid.shutdown()
            event.accept()
