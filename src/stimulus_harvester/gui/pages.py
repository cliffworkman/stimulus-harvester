"""Six workflow pages; input collection and presentation only."""

import copy
import json

from pydantic import ValidationError
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stimulus_harvester.curation import coverage_text, failure_text, summary_text
from stimulus_harvester.download.policy import wikimedia_host
from stimulus_harvester.screening.config import DetectorConfig
from stimulus_harvester.search.http import http_url
from stimulus_harvester.search.presentation import discovery_text, issue_message

from .components import (
    CandidateBrowser,
    Inspector,
    button,
    candidate_split,
    details,
    scroll_panel,
    show_json,
    text_label,
)


class Page(QWidget):
    def __init__(self, window, introduction):
        super().__init__()
        self.window = window
        self.box = QVBoxLayout(self)
        self.box.addWidget(text_label(introduction))

    def submit(self, action, **payload):
        self.window.submit(action, payload)


class ProjectPage(Page):
    def __init__(self, window):
        super().__init__(
            window,
            "Create a project or reopen an explicit project folder. Your current project remains editable after every freeze.",
        )
        form = QFormLayout()
        self.name = QLineEdit("My stimulus study")
        self.path = QLineEdit()
        self.path.setPlaceholderText("New/empty folder or existing project")
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        button(row, "Browse…", self.browse)
        form.addRow("Project name", self.name)
        form.addRow("Project folder", row)
        self.appearance = QComboBox()
        self.appearance.addItems(["System", "Light", "Dark"])
        self.appearance.currentTextChanged.connect(window.set_appearance)
        form.addRow("Appearance", self.appearance)
        self.box.addLayout(form)
        row = QHBoxLayout()
        button(row, "Create project", lambda: self.open_action("create"))
        button(row, "Open / migrate project", lambda: self.open_action("open"))
        button(row, "Create synthetic demo here", lambda: self.open_action("demo"))
        self.box.addLayout(row)
        self.summary = text_label(
            "No project open. The demo uses synthetic images and saved mock screening observations; no private images or models are needed."
        )
        self.box.addWidget(self.summary)
        row = QHBoxLayout()
        button(row, "Refresh project", lambda: self.submit("refresh"))
        button(row, "Check optional capabilities", self.check_dependencies)
        self.box.addLayout(row)
        self.capabilities = text_label("Checking optional capabilities…")
        self.box.addWidget(self.capabilities)
        self.tabs = QTabWidget()
        self.status = details()
        self.recovery = details()
        self.tabs.addTab(self.status, "Status & search coverage")
        self.tabs.addTab(self.recovery, "Recovery guidance")
        advanced = QWidget()
        layout = QVBoxLayout(advanced)
        layout.addWidget(
            text_label(
                "Advanced resolved configuration (JSON). Common discovery and screening settings have dedicated controls. Changes are validated and recorded by the backend."
            )
        )
        self.configuration = QPlainTextEdit()
        layout.addWidget(self.configuration)
        button(layout, "Validate & save advanced configuration", self.save_advanced)
        self.tabs.addTab(advanced, "Advanced settings")
        self.technical_status = details()
        self.tabs.addTab(self.technical_status, "Technical project evidence")
        self.box.addWidget(self.tabs, 1)
        self.loaded_config = None

    def browse(self):
        path = QFileDialog.getExistingDirectory(self, "Choose project folder", self.path.text())
        if path:
            self.path.setText(path)

    def open_action(self, action):
        if not self.path.text().strip():
            self.window.notice("Enter a project folder or use Browse.")
            return
        self.window.submit(action, {"name": self.name.text()}, path=self.path.text())

    def check_dependencies(self):
        config = self.window.session.state.config if self.window.session.state else None
        self.submit(
            "dependencies",
            probe=True,
            ollama_url=config["vlm"]["ollama_base_url"] if config else "http://localhost:11434",
        )

    def save_advanced(self):
        try:
            value = json.loads(self.configuration.toPlainText())
        except ValueError as exc:
            self.window.notice(f"Configuration JSON: {exc}")
            return
        self.submit("configure", configuration=value)

    def populate(self, state):
        self.path.setText(state.path)
        self.name.setText(state.name)
        human = state.status["curation"]
        show_json(
            self.technical_status,
            {
                "status": state.status,
                "providers": state.providers,
                "artifacts": state.artifacts,
                "recovery": state.recovery,
            },
        )
        self.summary.setText(summary_text(human["summary"]))
        self.status.setPlainText(
            failure_text(human["summary"]) + "\n\n" + coverage_text(human["coverage"])
        )
        self.recovery.setPlainText(
            "\n\n".join(r["guidance"] for r in state.recovery)
            or "No recovery entries detected. No automatic deletion or orphan adoption is performed."
        )
        if state.config != self.loaded_config:
            show_json(self.configuration, state.config)
            self.loaded_config = copy.deepcopy(state.config)


def provider_configuration_error(key, value):
    value = value.strip()
    if key == "searxng" and not http_url(value):
        return "Enter the HTTP(S) search URL of your SearXNG instance."
    if key == "commons" and not (
        http_url(value) or ("@" in value and not any(c.isspace() for c in value))
    ):
        return "Enter your operator email address or project URL for Commons."
    return ""


class ProviderSetupDialog(QDialog):
    def __init__(self, parent, key, value):
        super().__init__(parent)
        self.key = key
        self.setWindowTitle(
            "Configure SearXNG" if key == "searxng" else "Configure Wikimedia Commons"
        )
        layout = QVBoxLayout(self)
        layout.addWidget(
            text_label(
                "SearXNG needs a search endpoint from an instance you operate or have permission to use."
                if key == "searxng"
                else "Wikimedia Commons requires an operator contact in requests. Enter your email or project URL."
            )
        )
        self.value = QLineEdit(value)
        self.value.setAccessibleName("SearXNG endpoint" if key == "searxng" else "Commons contact")
        form = QFormLayout()
        form.addRow("Endpoint (required)" if key == "searxng" else "Contact (required)", self.value)
        layout.addLayout(form)
        self.error = text_label(provider_configuration_error(key, value))
        layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Use these settings")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.value.setFocus()
        self.value.selectAll()
        self.resize(520, self.sizeHint().height())

    def accept(self):
        error = provider_configuration_error(self.key, self.value.text())
        if error:
            self.error.setText(error)
            self.value.setFocus()
            return
        super().accept()


class DiscoverPage(Page):
    def __init__(self, window):
        super().__init__(
            window,
            "Find images and download originals. Source claims and failed downloads remain available for inspection.",
        )
        self.config = None
        self.provider_setup_dialog = None
        self.views = QTabWidget()
        self.box.addWidget(self.views, 1)
        setup = QWidget()
        setup_box = QVBoxLayout(setup)
        self.views.addTab(scroll_panel(setup), "Queries & providers")
        controls = QGroupBox("Search settings")
        form = QFormLayout(controls)
        self.seeds = QPlainTextEdit()
        self.seeds.setMaximumHeight(70)
        self.seeds.setPlaceholderText("One query / seed per line")
        form.addRow("Queries / seeds", self.seeds)
        self.providers = {}
        self.limits = {}
        for key, title, maximum in (
            ("openverse", "Openverse", 500),
            ("searxng", "SearXNG", 10000),
            ("commons", "Wikimedia Commons", 10),
        ):
            row = QHBoxLayout()
            enabled = QCheckBox(title)
            pages = QSpinBox()
            pages.setRange(1, 100)
            pages.setPrefix("Pages: ")
            count = QSpinBox()
            count.setRange(1, maximum)
            count.setPrefix("Results/query: " if key == "searxng" else "Results/page: ")
            row.addWidget(enabled)
            row.addWidget(pages)
            row.addWidget(count)
            form.addRow(row)
            self.providers[key] = enabled
            self.limits[key] = (pages, count)
        self.endpoint = QLineEdit()
        self.endpoint.setPlaceholderText("https://your-searxng-instance/search")
        self.contact = QLineEdit()
        self.contact.setPlaceholderText("Operator email or project URL (required for Commons)")
        form.addRow("SearXNG endpoint", self.endpoint)
        form.addRow("Commons contact", self.contact)
        for key in ("searxng", "commons"):
            self.providers[key].clicked.connect(
                lambda checked, key=key: self.configure_provider(key) if checked else None
            )
        form.addRow(
            text_label(
                "Enable SearXNG or Commons to configure it. Unconfigured providers stay unchecked. "
                "Changes are saved with Start a new search. Download actions also save needed Commons contact settings."
            )
        )
        expansion = QHBoxLayout()
        self.expand = QCheckBox("Deterministic query expansion")
        self.query_limit = QSpinBox()
        self.query_limit.setRange(1, 10000)
        self.query_limit.setPrefix("Max queries: ")
        expansion.addWidget(self.expand)
        expansion.addWidget(self.query_limit)
        form.addRow(expansion)
        self.synonyms = QLineEdit()
        self.synonyms.setPlaceholderText("Groups separated by ; and synonyms by ,")
        self.templates = QLineEdit()
        self.templates.setPlaceholderText("Templates separated by ; e.g. {0} interior")
        form.addRow("Synonym groups", self.synonyms)
        form.addRow("Phrase templates", self.templates)
        actions = QHBoxLayout()
        button(
            actions,
            "Preview queries & setup",
            lambda: self.submit("preview_queries", configuration=self.configuration()),
        )
        button(
            actions,
            "Start a new search",
            self.discover,
        )
        self.download_button = button(actions, "Download pending images", self.download_pending)
        self.retry_button = button(actions, "Retry eligible failures", self.retry_failed)
        button(actions, "Coverage & failures", lambda: self.submit("diagnostics"))
        form.addRow(actions)
        setup_box.addWidget(controls)
        self.provider_status = text_label()
        setup_box.addWidget(self.provider_status)
        self.acquisition_summary = text_label()
        self.box.insertWidget(1, self.acquisition_summary)
        # The persistent window summary already shows these counts on every page.
        self.acquisition_summary.hide()
        self.discovery_summary = text_label(
            "Preview setup before discovery. Detailed requests and overlap evidence remain available in diagnostics."
        )
        setup_box.addWidget(self.discovery_summary)
        setup_box.addStretch()
        self.browser = CandidateBrowser()
        self.inspector = Inspector()
        self.browser.inspect.connect(lambda cid: window.inspect(self.inspector, cid))
        self.views.addTab(candidate_split(self.browser, self.inspector), "Images & sources")
        self.query_preview = details()
        self.views.addTab(self.query_preview, "Coverage & failures")

    def download_pending(self):
        self.download_action("download")

    def retry_failed(self):
        self.download_action("retry-downloads")

    def download_action(self, action):
        if self.provider_setup_dialog is not None:
            return
        state = self.window.session.state
        payload = (
            {"candidate_ids": state.status["curation"]["summary"]["retry_ids"]}
            if action == "retry-downloads"
            else {}
        )
        selected = [
            c
            for c in state.candidates
            if (
                c["candidate_id"] in payload["candidate_ids"]
                if payload
                else c["download_status"] is None
            )
        ]
        if any(wikimedia_host(c.get("image_url", "")) for c in selected):
            previously_enabled = self.providers["commons"].isChecked()
            accepted = self.configure_provider("commons")
            self.providers["commons"].setChecked(previously_enabled)
            if not accepted:
                return
            contact = self.contact.text().strip()
            if contact != self.config["search"]["commons"]["contact"]:
                config = self.configuration()
                config["search"]["commons"]["contact"] = contact
                self.window.configure_then(action, config, payload=payload)
                return
        self.submit(action, **payload)

    def configure_provider(self, key):
        if self.provider_setup_dialog is not None:
            self.provider_setup_dialog.raise_()
            self.provider_setup_dialog.activateWindow()
            return False
        field = self.endpoint if key == "searxng" else self.contact
        if not provider_configuration_error(key, field.text()):
            return True
        self.views.setCurrentIndex(0)
        dialog = ProviderSetupDialog(self, key, field.text())
        self.provider_setup_dialog = dialog
        self.providers[key].setChecked(False)
        accepted = False
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            if accepted:
                field.setText(dialog.value.text().strip())
        finally:
            self.providers[key].setChecked(accepted)
            self.provider_setup_dialog = None
            dialog.deleteLater()
        return accepted

    def discover(self):
        if self.provider_setup_dialog is not None:
            return
        # Recheck edited values before saving or issuing any provider request.
        for key in ("searxng", "commons"):
            if self.providers[key].isChecked() and not self.configure_provider(key):
                return
        self.window.configure_then("search", self.configuration())

    def configuration(self):
        config = copy.deepcopy(self.config)
        search = config["search"]
        search["seed_queries"] = [
            s.strip() for s in self.seeds.toPlainText().splitlines() if s.strip()
        ]
        for key, check in self.providers.items():
            search[key]["enabled"] = check.isChecked()
            pages, count = self.limits[key]
            search[key]["max_pages_per_query"] = pages.value()
            search[key]["max_results_per_query" if key == "searxng" else "page_size"] = (
                count.value()
            )
        search["searxng"]["endpoint_url"] = self.endpoint.text().strip() or None
        search["commons"]["contact"] = self.contact.text().strip() or None
        search["query_expansion"].update(
            enabled=self.expand.isChecked(),
            max_expanded_queries=self.query_limit.value(),
            synonym_groups=[
                [s.strip() for s in group.split(",") if s.strip()]
                for group in self.synonyms.text().split(";")
                if group.strip()
            ],
            phrase_templates=[s.strip() for s in self.templates.text().split(";") if s.strip()],
        )
        return config

    def populate(self, state):
        self.browser.populate(state.candidates, state.path)
        counts = state.status["acquisition"]
        human = state.status["curation"]
        self.acquisition_summary.setText(summary_text(human["summary"]))
        self.query_preview.setPlainText(
            failure_text(human["summary"]) + "\n\n" + coverage_text(human["coverage"])
        )
        self.download_button.setEnabled(counts["pending"] > counts["pending_deferred"])
        self.retry_button.setEnabled(bool(human["summary"]["retry_ids"]))
        self.provider_status.setText(
            " · ".join(
                f"{name}: {entry['state']} — {entry['message']}"
                for name, entry in state.providers.items()
            )
        )
        if state.config and state.config != self.config:
            self.config = copy.deepcopy(state.config)
            search = self.config["search"]
            self.seeds.setPlainText("\n".join(search["seed_queries"]))
            self.endpoint.setText(search["searxng"]["endpoint_url"] or "")
            self.contact.setText(search["commons"]["contact"] or "")
            for key, check in self.providers.items():
                value = self.endpoint.text() if key == "searxng" else self.contact.text()
                check.setChecked(
                    search[key]["enabled"] and not provider_configuration_error(key, value)
                )
                pages, count = self.limits[key]
                pages.setValue(search[key]["max_pages_per_query"])
                count.setValue(
                    search[key]["max_results_per_query" if key == "searxng" else "page_size"]
                )
            expansion = search["query_expansion"]
            self.expand.setChecked(expansion["enabled"])
            self.query_limit.setValue(expansion["max_expanded_queries"])
            self.synonyms.setText("; ".join(", ".join(g) for g in expansion["synonym_groups"]))
            self.templates.setText("; ".join(expansion["phrase_templates"]))

    def report(self, action, value, status="succeeded"):
        if action == "preview_queries":
            lines = ["Expanded queries (deterministic):"] + [
                f"{i + 1}. {query} [seed: {seed}]"
                for i, (seed, query) in enumerate(value["queries"])
            ]
            lines += ["\nProvider setup:"] + [
                f"{name}: {r['state']} — {r['message']}" for name, r in value["providers"].items()
            ]
            self.query_preview.setPlainText("\n".join(lines))
            self.views.setCurrentIndex(2)
        elif action == "diagnostics":
            coverage = value["coverage"]
            lines = [
                f"Provider order: {', '.join(coverage['provider_order'])}",
                coverage["interpretation"],
            ]
            for provider, row in coverage["providers"].items():
                lines.append(
                    f"{provider}: {row['candidate_count']} found results; {row['incremental_unique_urls']} incremental URLs; {row['incremental_unique_assets']} incremental acquired assets"
                )
            lines += [
                f"{' / '.join(r['providers'])}: {r['shared_urls']} shared URLs; {r['shared_exact_assets']} exact shared assets"
                for r in coverage["pairwise_overlap"]
            ]
            lines += ["\nProvider/query failures:"] + [
                f"{r.get('provider')}: {r.get('query', 'setup')} — {issue_message(r)}"
                for r in value["events"]
                if r.get("status") in {"failed", "engine_errors"}
            ]
            human = self.window.session.state.status["curation"]
            self.query_preview.setPlainText(
                failure_text(human["summary"])
                + "\n\n"
                + coverage_text(human["coverage"])
                + "\n\n"
                + "\n".join(lines)
            )
            self.views.setCurrentIndex(2)
        elif action == "search":
            self.discovery_summary.setText(discovery_text(value, status))
            human = self.window.session.state.status["curation"]
            self.query_preview.setPlainText(
                discovery_text(value, status)
                + "\n\n"
                + failure_text(human["summary"])
                + "\n\n"
                + coverage_text(human["coverage"])
            )
            self.views.setCurrentIndex(2)
        elif action in {"download", "retry-downloads"}:
            self.discovery_summary.setText(
                f"This download operation: {value[0]} succeeded; {value[1]} failed. "
                "Previous successful images were not requested again. "
                "See Coverage & failures for eligible retries and failures that need a settings or format change."
            )


class ScreenPage(Page):
    def __init__(self, window):
        super().__init__(
            window,
            "Screening records observations and applies explicit project rules. not_detected ≠ confirmed absence. Investigator acceptance is still required.",
        )
        self.configured = text_label(
            "Unconfigured — no production detector or thresholds are selected."
        )
        self.box.addWidget(self.configured)
        self.views = QTabWidget()
        self.box.addWidget(self.views, 1)
        setup = QWidget()
        setup_box = QVBoxLayout(setup)
        self.views.addTab(scroll_panel(setup), "Configuration & rule")
        group = QGroupBox("Explicit human-presence configuration")
        form = QFormLayout(group)
        self.model = QComboBox()
        self.model.addItem("Choose a detector (no default)", None)
        self.model.addItem("Faster R-CNN ResNet50 FPN v2", "fasterrcnn_resnet50_fpn_v2")
        self.model.addItem(
            "Faster R-CNN MobileNet v3 large 320 FPN", "fasterrcnn_mobilenet_v3_large_320_fpn"
        )
        self.low = QLineEdit()
        self.low.setPlaceholderText("Explicit low threshold")
        self.high = QLineEdit()
        self.high.setPlaceholderText("Explicit high threshold")
        self.device = QComboBox()
        self.device.addItems(["cpu", "cuda"])
        self.policy = QComboBox()
        self.policy.addItem("Choose a project rule", None)
        self.policy.addItem(
            "Strict zero-human: detected → excluded; all others → review_required",
            "strict_zero_human",
        )
        self.policy.addItem("All observation states → review_required", "review_all")
        for name, field in (
            ("Detector", self.model),
            ("Low threshold", self.low),
            ("High threshold", self.high),
            ("Device", self.device),
            ("Rule", self.policy),
        ):
            form.addRow(name, field)
        row = QHBoxLayout()
        button(row, "Save explicit policy", self.save_policy)
        button(row, "Disable configured screening", self.disable_screening)
        self.run_button = button(row, "Run configured screening", self.run_screening)
        form.addRow(row)
        setup_box.addWidget(group)
        self.counts = text_label()
        setup_box.addWidget(self.counts)
        self.rule_details = details()
        technical = QCheckBox("Technical policy details")
        setup_box.addWidget(technical)
        setup_box.addWidget(self.rule_details, 1)
        self.rule_details.hide()
        technical.toggled.connect(self.rule_details.setVisible)
        self.browser = CandidateBrowser(available_only=True)
        self.inspector = Inspector()
        self.browser.inspect.connect(lambda cid: window.inspect(self.inspector, cid))
        self.views.addTab(
            candidate_split(self.browser, self.inspector), "Saved observations & images"
        )
        self.config = None

    def run_screening(self):
        if self.window.confirm(
            "Run configured screening?",
            "This explicitly starts local inference. Missing pretrained weights download on first use "
            "(about 167 MiB for ResNet50 v2 or 74 MiB for MobileNet320) to the user's Torch cache. "
            "No weights are included in the core installer. Exact weight identity and SHA-256 are "
            "recorded with observations. Detector scores do not establish human absence.",
        ):
            self.submit("screen")

    def save_policy(self):
        model = self.model.currentData()
        if model is None:
            self.window.notice(
                "No detector selected. Saved screening settings are unchanged. "
                "Screening is optional; use Disable configured screening to remove an existing policy."
            )
            self.model.setFocus()
            return
        for field, label in ((self.low, "low"), (self.high, "high")):
            if not field.text().strip():
                self.window.notice(
                    f"Enter an explicit {label} threshold for the selected detector. Nothing was saved."
                )
                field.setFocus()
                return
        try:
            detector = DetectorConfig(
                id="desktop-human",
                property="human_presence",
                model=model,
                low_threshold=self.low.text(),
                high_threshold=self.high.text(),
                device=self.device.currentText(),
            )
        except ValidationError as exc:
            self.window.notice(f"Invalid screening settings; nothing was saved: {exc}")
            return
        policy = self.policy.currentData()
        if policy is None:
            self.window.notice("Choose an explicit screening rule. Nothing was saved.")
            self.policy.setFocus()
            return
        if not self.window.confirm(
            "Replace screening configuration?",
            "Save the explicitly selected detector, thresholds and rule. Existing observations are preserved; this does not run inference. Custom policies can be edited in Project → Advanced settings.",
        ):
            return
        self.submit(
            "screen-policy",
            model=detector.model,
            low_threshold=detector.low_threshold,
            high_threshold=detector.high_threshold,
            policy=policy,
            device=detector.device,
        )

    def disable_screening(self):
        if self.config and self.window.confirm(
            "Disable configured screening?",
            "Save and activate an empty screening policy without inference. Existing observations are preserved; effective dispositions are recomputed from the empty policy. No detector default will be inserted.",
        ):
            config = copy.deepcopy(self.config)
            config["screening"] = {}
            self.window.configure_then("screen", config)

    def populate(self, state):
        self.browser.populate(state.candidates, state.path)
        report = state.screening
        show_json(
            self.rule_details,
            {
                "active_evidence_policy": report["policy"],
                "configured_policy": (state.config or {}).get("screening"),
            },
        )
        self.config = state.config
        detectors = (state.config or {}).get("screening", {}).get("detectors", [])
        self.configured.setText(
            (
                "Configured: "
                + "; ".join(
                    f"{d['id']} · {d['model']} · {d['low_threshold']} / {d['high_threshold']}"
                    for d in detectors
                )
                if detectors
                else "Unconfigured — no detector or threshold default selected."
            )
        )
        counts = report["observation_counts"]
        self.counts.setText(
            "Saved observations: "
            + " · ".join(
                f"{s}: {counts.get(s, 0)}"
                for s in ("detected", "uncertain", "not_detected", "error", "not_run")
            )
            + "\nEffective dispositions: "
            + " · ".join(
                f"{s}: {report['counts'][s]}" for s in ("eligible", "excluded", "review_required")
            )
            + "\n"
            + report["observation_count_basis"]
        )
        if len(detectors) == 1:
            detector = detectors[0]
            self.model.setCurrentIndex(max(0, self.model.findData(detector["model"])))
            self.low.setText(str(detector["low_threshold"]))
            self.high.setText(str(detector["high_threshold"]))
            self.device.setCurrentText(detector["device"])
        elif not detectors:
            self.model.setCurrentIndex(0)
            self.low.clear()
            self.high.clear()
            self.policy.setCurrentIndex(0)
        self.refresh_capability()

    def refresh_capability(self):
        available = self.window.dependencies.get("person_detector", {}).get("available", False)
        configured = any(
            d["enabled"] for d in (self.config or {}).get("screening", {}).get("detectors", [])
        )
        self.run_button.setEnabled(available and configured)
        self.run_button.setToolTip(
            "Requires an explicitly configured detector and optional torchvision/PyTorch runtime. Check capabilities in Project."
        )


class FreezePanel(QWidget):
    def __init__(self, window, reviewer):
        super().__init__()
        self.window, self.reviewer = window, reviewer
        self.validated_request = None
        self.validation = None
        self.resolutions = {}
        self.project_id = None
        box = QVBoxLayout(self)
        box.addWidget(
            text_label(
                "Freeze preparation — current project. Resolve every blocker, then confirm an immutable snapshot. Changing the live project later requires a new freeze."
            )
        )
        form = QFormLayout()
        self.scope = QComboBox()
        self.scope.addItems(["All current candidates", "Selected candidates"])
        self.scope_rule = QLineEdit("All current candidates at validation")
        self.selected = QListWidget()
        self.selected.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.selected.setMaximumHeight(115)
        self.selected.hide()
        self.scope.currentIndexChanged.connect(lambda i: self.selected.setVisible(i == 1))
        self.parent = QComboBox()
        self.parent.addItem("New independent freeze", None)
        form.addRow("Scope", self.scope)
        form.addRow("Selection rule", self.scope_rule)
        form.addRow(self.selected)
        form.addRow("Parent freeze (revision)", self.parent)
        box.addLayout(form)
        self.deduplicate = QCheckBox(
            "Explicitly materialize identical accepted bytes once (preserve all histories)"
        )
        box.addWidget(self.deduplicate)
        row = QHBoxLayout()
        button(row, "Validate freeze", self.validate)
        self.create_button = button(row, "Confirm & create freeze…", self.create)
        self.create_button.setEnabled(False)
        box.addLayout(row)
        self.summary = text_label(
            "Run validation to see the exact scope, blockers and proposed membership."
        )
        box.addWidget(self.summary)
        self.blockers = QTreeWidget()
        self.blockers.setMaximumHeight(130)
        self.blockers.setHeaderLabels(["Blocker / warning", "Candidate / asset", "Explanation"])
        self.blockers.itemDoubleClicked.connect(self.navigate_blocker)
        box.addWidget(self.blockers, 1)
        self.duplicates = QTreeWidget()
        self.duplicates.setMaximumHeight(170)
        self.duplicates.setHeaderLabels(
            ["Shared asset / candidate", "Decision / screening", "Sources / consequence"]
        )
        for tree, widths in ((self.blockers, (190, 245, 520)), (self.duplicates, (280, 210, 500))):
            for column, width in enumerate(widths):
                tree.setColumnWidth(column, width)
        self.duplicates.itemDoubleClicked.connect(self.navigate_blocker)
        box.addWidget(self.duplicates, 1)
        group = QGroupBox("Explicit shared-asset membership adjudication")
        form = QFormLayout(group)
        self.asset = QComboBox()
        self.asset.addItem("Choose a conflicting shared asset", None)
        self.membership = QComboBox()
        self.membership.addItem("Choose consequence", None)
        self.membership.addItem("Include the accepted asset once", "include")
        self.membership.addItem("Omit the shared asset from assembly", "omit")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Scientific reason; original decisions remain unchanged")
        form.addRow("Asset", self.asset)
        form.addRow("Membership", self.membership)
        form.addRow("Reason", self.reason)
        button(form, "Record resolution in this freeze request", self.resolve_duplicate)
        self.resolution_summary = text_label("No membership adjudications recorded.")
        form.addRow(self.resolution_summary)
        box.addWidget(group)
        for signal in (
            self.scope.currentIndexChanged,
            self.scope_rule.textChanged,
            self.selected.itemSelectionChanged,
            self.parent.currentIndexChanged,
            self.deduplicate.toggled,
            reviewer.textChanged,
        ):
            signal.connect(self.invalidate)

    def invalidate(self, *args):
        self.validated_request = None
        self.create_button.setEnabled(False)

    def request(self):
        scope = {
            "mode": "all_current" if self.scope.currentIndex() == 0 else "candidates",
            "selection_rule": self.scope_rule.text(),
        }
        if self.scope.currentIndex() == 1:
            scope["candidate_ids"] = [
                i.data(Qt.ItemDataRole.UserRole) for i in self.selected.selectedItems()
            ]
        return {
            "reviewer": self.reviewer.text(),
            "scope": scope,
            "parent_freeze_id": self.parent.currentData(),
            "deduplicate_accepted": self.deduplicate.isChecked(),
            "duplicate_resolutions": list(self.resolutions.values()),
        }

    def validate(self):
        self.window.pending_freeze_request = self.request()
        self.window.submit("validate", {"request": self.window.pending_freeze_request})

    def receive_validation(self, report, request):
        self.validation = report
        self.validated_request = copy.deepcopy(request) if report["valid"] else None
        self.create_button.setEnabled(report["valid"])
        self.summary.setText(
            f"{'Ready for confirmation' if report['valid'] else 'Blocked'} · {report['scope_count']} candidates · {report['assembled_asset_count']} proposed assets\n"
            f"Decisions: {report['summary']['reviews']} · {len(report['blockers'])} blockers · {len(report['warnings'])} warnings\nDouble-click a candidate blocker or duplicate history to review it."
        )
        self.blockers.clear()
        for record in report["blockers"] + report["warnings"]:
            item = QTreeWidgetItem(
                [
                    record["code"],
                    record.get("candidate_id", record.get("asset_id", "")),
                    record["message"],
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, record.get("candidate_id"))
            self.blockers.addTopLevelItem(item)
            for column in range(item.columnCount()):
                item.setToolTip(column, item.text(column))
        self.duplicates.clear()
        self.asset.clear()
        self.asset.addItem("Choose a conflicting shared asset", None)
        candidates = {c["candidate_id"]: c for c in self.window.session.state.candidates}
        for group in report["duplicates"]:
            resolution = group.get("resolution")
            root = QTreeWidgetItem(
                [
                    group["asset_id"],
                    "Conflict" if group["membership_conflict"] else "Shared bytes",
                    str(resolution or "Explicit deduplication needed for multiple accepts"),
                ]
            )
            for decision in group["candidate_decisions"]:
                candidate = candidates[decision["candidate_id"]]
                sources = "; ".join(
                    f"{s['provider']}: {s['license'] or 'unknown'}" for s in candidate["sources"]
                )
                item = QTreeWidgetItem(
                    [
                        candidate["primary_local_id"]
                        + (" (outside scope)" if not decision["in_scope"] else ""),
                        f"{decision['label']} / {candidate['screening']['effective_disposition']}",
                        sources,
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, decision["candidate_id"])
                for column in range(item.columnCount()):
                    item.setToolTip(column, item.text(column))
                root.addChild(item)
            self.duplicates.addTopLevelItem(root)
            root.setExpanded(True)
            if group["membership_conflict"]:
                self.asset.addItem(group["asset_id"], group["asset_id"])

    def resolve_duplicate(self):
        aid = self.asset.currentData()
        if (
            not aid
            or not self.membership.currentData()
            or not self.reason.text().strip()
            or not self.reviewer.text().strip()
        ):
            self.window.notice(
                "Choose a conflicting asset and include/omit consequence; enter investigator identity and reason."
            )
            return
        self.resolutions[aid] = {
            "asset_id": aid,
            "membership": self.membership.currentData(),
            "reviewer": self.reviewer.text(),
            "reason": self.reason.text(),
        }
        self.resolution_summary.setText(
            "\n".join(
                f"{r['asset_id']}: {r['membership']} — {r['reason']} ({r['reviewer']})"
                for r in self.resolutions.values()
            )
        )
        self.invalidate()
        self.window.notice(
            "Resolution added to this request. Validate again to inspect the resulting membership."
        )

    def navigate_blocker(self, item, column):
        cid = item.data(0, Qt.ItemDataRole.UserRole)
        if cid:
            self.window.navigate_candidate(cid)

    def create(self):
        if not self.validated_request or not self.validation:
            self.window.notice("Validate the current freeze request first.")
            return
        summary = json.dumps(self.validation["summary"], indent=2, ensure_ascii=False)
        if self.window.confirm(
            "Create immutable freeze?",
            summary
            + "\n\nThis captures a new scientific snapshot. Existing freezes will not change.",
        ):
            self.window.submit(
                "freeze-create",
                {
                    "request": copy.deepcopy(self.validated_request),
                    "expected_snapshot_sha256": self.validation["snapshot_sha256"],
                },
            )

    def populate(self, state):
        if self.project_id != state.project_id:
            self.resolutions.clear()
            self.resolution_summary.setText("No membership adjudications recorded.")
            self.project_id = state.project_id
        selected = {i.data(Qt.ItemDataRole.UserRole) for i in self.selected.selectedItems()}
        self.selected.blockSignals(True)
        self.selected.clear()
        for c in state.candidates:
            item = QListWidgetItem(f"{c['primary_local_id']} · {c['label'] or 'unrated'}")
            item.setData(Qt.ItemDataRole.UserRole, c["candidate_id"])
            item.setToolTip(c["candidate_id"])
            self.selected.addItem(item)
            item.setSelected(c["candidate_id"] in selected)
        self.selected.blockSignals(False)
        parent = self.parent.currentData()
        self.parent.blockSignals(True)
        self.parent.clear()
        self.parent.addItem("New independent freeze", None)
        for artifact in state.artifacts:
            if artifact["kind"] == "freeze":
                self.parent.addItem(
                    f"{artifact['created_at']} · {artifact['artifact_id']}", artifact["artifact_id"]
                )
        self.parent.setCurrentIndex(max(0, self.parent.findData(parent)))
        self.parent.blockSignals(False)
        self.invalidate()


class ReviewPage(Page):
    def __init__(self, window):
        super().__init__(
            window,
            "Viewing images does not record a review. View filters never change freeze membership.",
        )
        self.box.itemAt(0).widget().hide()
        self.setToolTip(
            "Viewing images never records a review. View filters never change freeze membership."
        )
        identity = QHBoxLayout()
        self.reviewer = QLineEdit()
        self.reviewer.setPlaceholderText("Investigator name or email (required)")
        self.reviewer.setAccessibleName("Investigator identity required for a review decision")
        identity.addWidget(text_label("Investigator"))
        identity.addWidget(self.reviewer, 1)
        self.notes = QLineEdit()
        self.notes.setPlaceholderText("Notes for this decision (optional)")
        self.notes.setAccessibleName("Notes for the current review decision")
        identity.addWidget(self.notes, 1)
        self.box.addLayout(identity)
        self.modes = QTabWidget()
        self.box.addWidget(self.modes, 1)
        review = QWidget()
        layout = QVBoxLayout(review)
        self.browser = CandidateBrowser(available_only=True, triage=True)
        self.inspector = Inspector()
        self.browser.inspect.connect(lambda cid: window.inspect(self.inspector, cid))
        layout.addWidget(candidate_split(self.browser, self.inspector), 1)
        row = QHBoxLayout()
        for label, title, key in (
            ("accepted", "Accept", "Alt+A"),
            ("maybe", "Maybe", "Alt+M"),
            ("rejected", "Reject", "Alt+R"),
        ):
            action = button(row, title, lambda checked=False, label=label: self.review(label))
            action.setToolTip(f"{title} ({key})")
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda label=label: self.review(label) if self.modes.currentIndex() == 0 else None
            )
        self.browser.layout().addLayout(row)
        self.advance = QCheckBox("Advance")
        self.advance.setToolTip("After an explicit decision, move to the next image in this view")
        self.advance.setAccessibleName("After a decision, move to the next image")
        self.next_after_review = None
        batch_row = QHBoxLayout()
        button(batch_row, "Select", self.mark_current)
        self.batch_button = button(batch_row, "Review selected (0)…", self.batch_review)
        self.batch_button.setEnabled(False)
        batch_row.addWidget(self.advance)
        self.browser.marks_changed.connect(self.update_marks)
        self.browser.layout().addLayout(batch_row)
        override_widget = QWidget()
        override = QHBoxLayout(override_widget)
        self.disposition = QComboBox()
        self.disposition.addItem("Choose explicit screening override", None)
        for value in ("review_required", "eligible", "excluded"):
            self.disposition.addItem(value, value)
        self.override_note = QLineEdit()
        self.override_note.setPlaceholderText("Reason for screening adjudication")
        override.addWidget(self.disposition)
        override.addWidget(self.override_note, 1)
        button(override, "Record screening override…", self.override)
        self.modes.addTab(review, "Current review")
        self.freeze = FreezePanel(window, self.reviewer)
        self.modes.addTab(scroll_panel(self.freeze), "Prepare freeze")
        self.modes.addTab(scroll_panel(override_widget), "Screening override (advanced)")

    def review(self, label):
        current = self.inspector.current
        if not current or current["candidate_id"] != self.browser.candidate_id():
            self.window.notice("Select and inspect an image first.")
            return
        ids = self.browser.visible_ids
        index = ids.index(current["candidate_id"]) if current["candidate_id"] in ids else -1
        self.next_after_review = (
            ids[index + 1] if self.advance.isChecked() and 0 <= index < len(ids) - 1 else None
        )
        self.submit(
            "review",
            local_id=current["evidence"]["primary_local_id"],
            label=label,
            notes=self.notes.text(),
            reviewer_email=self.reviewer.text(),
        )

    def mark_current(self):
        cid = self.browser.candidate_id()
        if cid:
            self.browser.mark(cid, cid not in self.browser.marked_ids)
            self.browser.refresh_grid()

    def update_marks(self):
        n = len(self.browser.marked_ids)
        self.batch_button.setText(f"Review selected ({n})…")
        self.batch_button.setEnabled(bool(n))

    def batch_review(self):
        rows = [
            self.browser.rows[cid]
            for cid in self.browser.visible_ids
            if cid in self.browser.marked_ids
        ]
        if not rows:
            return
        if not self.reviewer.text().strip():
            self.window.notice("Enter the investigator identity before recording a batch review.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Preview exact batch scope")
        dialog.resize(600, 450)
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            text_label(
                f"{len(rows)} explicitly selected candidates. Other histories of the same bytes are not included automatically.\n"
                "Maybe keeps images for further inspection; it is not acceptance. Reject records investigator rejection. "
                "Later decisions supersede these records without deleting history. Screening and frozen artifacts do not change."
            )
        )
        scope = QPlainTextEdit()
        scope.setReadOnly(True)
        scope.setPlainText(
            "\n\n".join(f"{r['title']} · {r['review_text']}\n{r['candidate_id']}" for r in rows)
        )
        layout.addWidget(scope, 1)
        choice = QComboBox()
        choice.addItem("Choose a decision", None)
        choice.addItem("Maybe — inspect further", "maybe")
        choice.addItem("Reject", "rejected")
        layout.addWidget(choice)
        notes = QLineEdit()
        notes.setPlaceholderText("Note applied to every selected candidate")
        layout.addWidget(notes)
        layout.addWidget(text_label(f"Investigator: {self.reviewer.text().strip()}"))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Record this batch")
        buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)
        choice.currentIndexChanged.connect(
            lambda: buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(
                bool(choice.currentData())
            )
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        clear = buttons.addButton("Clear selection", QDialogButtonBox.ButtonRole.ResetRole)
        clear.clicked.connect(lambda: (self.browser.clear_marks(), dialog.reject()))
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.submit(
            "review-batch",
            selection=[{k: r[k] for k in ("candidate_id", "asset_id", "label")} for r in rows],
            label=choice.currentData(),
            notes=notes.text(),
            reviewer_email=self.reviewer.text().strip(),
        )

    def override(self):
        current = self.inspector.current
        if (
            not current
            or not self.disposition.currentData()
            or not self.override_note.text().strip()
        ):
            self.window.notice(
                "Inspect a candidate, choose an explicit disposition and enter a reason."
            )
            return
        screening = current["evidence"]["screening"]["candidates"][0]
        if self.window.confirm(
            "Record screening override?",
            f"{current['candidate_id']}\n{screening['effective_disposition']} → {self.disposition.currentData()}\n{self.override_note.text()}\nThis is an investigator adjudication, separate from observations and rules.",
        ):
            self.submit(
                "screen-override",
                candidate_id=current["candidate_id"],
                disposition=self.disposition.currentData(),
                reviewer=self.reviewer.text(),
                note=self.override_note.text(),
                expected_scope=screening["scope"],
            )

    def populate(self, state):
        self.browser.populate(state.candidates, state.path)
        self.freeze.populate(state)


class ArtifactPage(Page):
    def __init__(self, window, exporting=False):
        super().__init__(
            window,
            "Frozen artifact history — read-only. "
            + (
                "Export preserves every retained source license/attribution claim; it does not infer legal permission."
                if exporting
                else "Assembly uses only the selected freeze and exact acquired bytes. Current review decisions and accepted-folder copies do not determine membership."
            ),
        )
        self.exporting = exporting
        self.current_artifact = None
        self.freeze = QComboBox()
        self.assembly = QComboBox()
        form = QFormLayout()
        form.addRow("Freeze", self.freeze)
        form.addRow(
            "Assembly" if exporting else "Prior assembly to reproduce (optional)", self.assembly
        )
        self.box.addLayout(form)
        self.freeze.currentIndexChanged.connect(self.fill_assemblies)
        row = QHBoxLayout()
        button(row, "Inspect selected freeze", self.inspect_freeze)
        self.action_button = button(
            row,
            "Create frozen research package" if exporting else "Assemble selected freeze",
            self.run_action,
        )
        button(row, "Open verified output folder", self.open_folder)
        self.box.addLayout(row)
        self.summary = text_label(
            "Choose a freeze. No current-state assembly or mutable export is available here."
        )
        self.box.addWidget(self.summary)
        self.history = QTreeWidget()
        self.history.setHeaderLabels(
            ["Kind / created", "Artifact ID", "Parent / lineage", "Manifest SHA-256"]
        )
        self.history.itemDoubleClicked.connect(self.inspect_history)
        self.history.setMinimumHeight(130)
        for column, width in enumerate((190, 260, 260, 380)):
            self.history.setColumnWidth(column, width)
        self.box.addWidget(self.history, 1)
        self.artifact_views = QTabWidget()
        self.members = QTreeWidget()
        self.members.setHeaderLabels(
            ["Asset / output", "Accepted candidate IDs", "Sources / claims", "SHA-256"]
        )
        for column, width in enumerate((230, 260, 270, 300)):
            self.members.setColumnWidth(column, width)
        self.artifact_views.addTab(self.members, "Frozen membership")
        self.manifest = details()
        self.artifact_views.addTab(self.manifest, "Complete manifest (advanced)")
        self.box.addWidget(self.artifact_views, 2)
        self.artifacts = []

    def populate(self, state):
        self.artifacts = state.artifacts
        current = self.freeze.currentData()
        self.freeze.blockSignals(True)
        self.freeze.clear()
        self.freeze.addItem("Select an immutable freeze", None)
        for a in self.artifacts:
            if a["kind"] == "freeze":
                self.freeze.addItem(f"{a['created_at']} · {a['artifact_id']}", a["artifact_id"])
        self.freeze.setCurrentIndex(max(0, self.freeze.findData(current)))
        self.freeze.blockSignals(False)
        self.fill_assemblies()
        self.history.clear()
        for a in self.artifacts:
            row = QTreeWidgetItem(
                [
                    f"{a['kind']} · {a['created_at']}",
                    a["artifact_id"],
                    a["parent_id"] or a["freeze_id"],
                    a["manifest_sha256"],
                ]
            )
            row.setData(0, Qt.ItemDataRole.UserRole, a["artifact_id"])
            for column in range(row.columnCount()):
                row.setToolTip(column, row.text(column))
            self.history.addTopLevelItem(row)

    def fill_assemblies(self):
        prior = self.assembly.currentData()
        self.assembly.clear()
        self.assembly.addItem(
            "Create an assembly from selected freeze"
            if self.exporting
            else "No prior assembly comparison",
            None,
        )
        for a in self.artifacts:
            if a["kind"] == "assembly" and a["freeze_id"] == self.freeze.currentData():
                self.assembly.addItem(a["artifact_id"], a["artifact_id"])
        self.assembly.setCurrentIndex(max(0, self.assembly.findData(prior)))
        self.action_button.setEnabled(bool(self.freeze.currentData()))

    def inspect_freeze(self):
        if self.freeze.currentData():
            self.window.inspect_artifact(self, self.freeze.currentData())
        else:
            self.window.notice("Select a freeze first.")

    def inspect_history(self, item, column):
        self.window.inspect_artifact(self, item.data(0, Qt.ItemDataRole.UserRole))

    def run_action(self):
        freeze = self.freeze.currentData()
        if not freeze:
            self.window.notice("Select an immutable freeze first.")
            return
        if self.exporting:
            self.submit("export-frozen", freeze_id=freeze, assembly_id=self.assembly.currentData())
        else:
            self.submit(
                "assemble", freeze_id=freeze, parent_assembly_id=self.assembly.currentData()
            )

    def display(self, value):
        self.current_artifact = value
        m = value["manifest"]
        claims = m.get(
            "license_policy",
            m.get("semantics", {}).get("license_policy", "See frozen source evidence"),
        )
        self.summary.setText(
            f"Verified at inspection · {m['kind']} · {m['artifact_id']}\nCreated {m['created_at']} · {len(m['membership'])} assets · parent {m['parent_id'] or 'none'}\n{value['path']}\nLicense/source policy: {claims}"
        )
        show_json(self.manifest, value)
        self.members.clear()
        for member in m["membership"]:
            claims = "; ".join(
                f"{c['provider']}: {c['license'] or 'unknown'}"
                for c in member.get("source_claims", [])
            )
            row = QTreeWidgetItem(
                [
                    member.get("path", member["filename"]),
                    "\n".join(member["candidate_ids"]),
                    claims or f"{len(member['source_ids'])} source records (manifest)",
                    member["sha256"],
                ]
            )
            for column in range(row.columnCount()):
                row.setToolTip(column, row.text(column))
            self.members.addTopLevelItem(row)

    def open_folder(self):
        if self.current_artifact:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.current_artifact["path"]))
        else:
            self.window.notice("Inspect and verify an artifact first.")
