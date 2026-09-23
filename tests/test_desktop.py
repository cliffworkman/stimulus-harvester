"""Qt/service boundary tests. Synthetic fixtures, no network or real ML inference."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton

from stimulus_harvester import services, workspace
from stimulus_harvester.frozen.demo import config, request, run, seed
from stimulus_harvester.frozen.store import inventory
from stimulus_harvester.gui.adapter import Adapter, Reply, classify
from stimulus_harvester.gui.tasks import Worker
from stimulus_harvester.gui.window import Window


def settle(qtbot, window):
    for _ in range(4):
        qtbot.waitUntil(lambda: not window.session.busy, timeout=60000)
        qtbot.wait(30)


@pytest.fixture
def window(qtbot):
    value = Window()
    qtbot.addWidget(value)
    value.show()
    settle(qtbot, value)
    value.confirm = lambda *args: True
    return value


@pytest.fixture
def project(tmp_path):
    return services.create_project(tmp_path / "研究 project with spaces", config())


def open_window(qtbot, window, project):
    window.submit("open", path=str(project.root))
    settle(qtbot, window)


def test_six_stage_startup_and_no_default_without_ml(window, monkeypatch):
    monkeypatch.setattr(workspace.importlib.util, "find_spec", lambda name: None)
    caps = workspace.dependency_states()
    assert caps["core"]["available"] and not caps["person_detector"]["available"]
    assert window.tabs.count() == 6
    assert [window.tabs.tabText(i) for i in range(6)] == [
        "1  Project",
        "2  Discover",
        "3  Screen",
        "4  Review freeze",
        "5  Assemble",
        "6  Export",
    ]
    assert window.screen.model.currentData() is None
    assert window.screen.low.text() == window.screen.high.text() == ""
    assert not window.screen.run_button.isEnabled()


def test_create_reopen_unicode_project_via_widgets(qtbot, window, tmp_path):
    path = tmp_path / "Native 研究 study"
    window.project.path.setText(str(path))
    window.project.name.setText("Native research")
    window.project.open_action("create")
    settle(qtbot, window)
    identity = window.session.state.project_id
    window.project.open_action("open")
    settle(qtbot, window)
    assert window.session.state.project_id == identity
    assert window.session.state.path == str(path)
    assert window.discover.isEnabled() and window.review.isEnabled()
    assert window.session.state.config["screening"]["detectors"] == []


def test_discovery_controls_use_shared_configuration_and_search(
    qtbot, window, project, monkeypatch
):
    open_window(qtbot, window, project)
    called = []
    real = services.run_stage

    def stage(path, action, **kwargs):
        called.append(action)
        if action == "search":
            return services.OperationResult(
                "test",
                action,
                "partial",
                {
                    "pages_committed": 2,
                    "issues": [{"provider": "searxng", "error": "JSON unavailable"}],
                },
            )
        return real(path, action, **kwargs)

    monkeypatch.setattr(services, "run_stage", stage)
    window.discover.seeds.setPlainText("library interior\ncourtyard")
    window.configure_then("search", window.discover.configuration())
    settle(qtbot, window)
    assert called == ["configure", "search"]
    assert project.config.search.seed_queries == ["library interior", "courtyard"]
    assert "Partial discovery" in window.operation.text()
    assert "searxng" in window.log.toPlainText()


def test_provider_setup_errors_are_not_zero_results(project):
    value = project.config.model_dump(mode="json")
    value["search"]["searxng"]["enabled"] = True
    value["search"]["commons"]["enabled"] = True
    run(project, "configure", configuration=value)
    state = workspace.workspace(project.root)
    assert state.providers["searxng"]["state"] == "setup_error"
    assert state.providers["commons"]["state"] == "setup_error"
    assert "contact" in state.providers["commons"]["message"]


def test_unconfigured_provider_selection_does_not_rewrite_project(qtbot, window, project):
    original = project.paths.config_snapshot_path.read_bytes()
    open_window(qtbot, window, project)
    assert not window.discover.providers["searxng"].isChecked()
    assert not window.discover.providers["commons"].isChecked()
    assert project.paths.config_snapshot_path.read_bytes() == original


@pytest.mark.parametrize("key,value", [
    ("searxng", "https://search.example.invalid/search"),
    ("commons", "operator@example.invalid"),
])
def test_enabling_provider_routes_to_validated_configuration(qtbot, window, project, key, value, monkeypatch):
    import socket

    from stimulus_harvester.gui.pages import ProviderSetupDialog

    open_window(qtbot, window, project)
    window.tabs.setCurrentIndex(1)
    page = window.discover
    page.seeds.setPlainText("courtyard\n  library study  ")
    page.synonyms.setText("room,hall; quiet,empty")
    page.limits[key][0].setValue(7)
    before = page.configuration()
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Setup must not contact a provider"))
    monkeypatch.setattr(window, "configure_then", lambda *a: pytest.fail("Setup must not start discovery"))

    def configure():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ProviderSetupDialog) and dialog.key == key
        assert dialog.error.text() and dialog.value.accessibleName()
        assert not page.providers[key].isChecked()
        assert not page.configure_provider(key)
        page.discover()
        assert page.provider_setup_dialog is dialog
        assert len(page.findChildren(ProviderSetupDialog)) == 1
        dialog.value.setText("invalid value")
        qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)
        assert dialog.isVisible() and dialog.error.text()
        dialog.value.setText(value)
        qtbot.mouseClick(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)

    QTimer.singleShot(0, configure)
    qtbot.mouseClick(window.discover.providers[key], Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert window.discover.providers[key].isChecked()
    field = "endpoint_url" if key == "searxng" else "contact"
    before["search"][key].update({field: value, "enabled": True})
    assert page.configuration() == before
    assert page.seeds.toPlainText() == "courtyard\n  library study  "
    assert page.views.currentIndex() == 0 and page.provider_setup_dialog is None
    # Setup prepares the form, preserving the normal explicit save operation.
    assert getattr(getattr(project.config.search, key), field) is None


@pytest.mark.parametrize("key", ["searxng", "commons"])
def test_cancel_provider_setup_leaves_provider_unchecked(qtbot, window, project, key):
    open_window(qtbot, window, project)
    window.tabs.setCurrentIndex(1)
    page = window.discover
    page.seeds.setPlainText("Keep my current queries\n研究")
    before = page.configuration()

    def cancel_incomplete():
        dialog = QApplication.activeModalWidget()
        dialog.value.setText("invalid unfinished setting")
        dialog.accept()
        assert dialog.isVisible()
        dialog.reject()

    QTimer.singleShot(0, cancel_incomplete)
    qtbot.mouseClick(window.discover.providers[key], Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert not window.discover.providers[key].isChecked()
    assert not window.discover.configuration()["search"][key]["enabled"]
    assert page.configuration() == before
    assert page.views.currentIndex() == 0 and page.provider_setup_dialog is None


@pytest.mark.parametrize("key,value", [
    ("searxng", "https://search.example.invalid/search"),
    ("commons", "https://example.invalid/project"),
])
def test_configured_provider_can_be_enabled_without_prompt(qtbot, window, project, key, value, monkeypatch):
    from stimulus_harvester.gui.pages import ProviderSetupDialog

    open_window(qtbot, window, project)
    window.tabs.setCurrentIndex(1)
    field = window.discover.endpoint if key == "searxng" else window.discover.contact
    field.setText(value)
    monkeypatch.setattr(ProviderSetupDialog, "exec", lambda *a: pytest.fail("Valid setup must not prompt again"))
    qtbot.mouseClick(window.discover.providers[key], Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert window.discover.providers[key].isChecked()


@pytest.mark.parametrize("key", ["searxng", "commons"])
def test_discover_rechecks_provider_settings_before_saving_or_search(qtbot, window, project, key, monkeypatch):
    open_window(qtbot, window, project)
    window.discover.providers[key].setChecked(True)
    field = window.discover.endpoint if key == "searxng" else window.discover.contact
    field.clear()
    monkeypatch.setattr(window, "configure_then", lambda *a: pytest.fail("Cancelled setup must not save or search"))
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    window.discover.discover()
    assert not window.discover.providers[key].isChecked()


@pytest.mark.parametrize("key,value", [
    ("searxng", "https://search.example.invalid/search"),
    ("commons", "operator@example.invalid"),
])
def test_discovery_preflight_resumes_only_after_valid_setup(qtbot, window, project, key, value, monkeypatch):
    open_window(qtbot, window, project)
    page = window.discover
    page.providers[key].setChecked(True)
    calls = []
    monkeypatch.setattr(window, "configure_then", lambda *a: calls.append(a))

    def configure():
        assert not calls
        page.discover()
        assert not calls
        dialog = QApplication.activeModalWidget()
        assert dialog.key == key
        dialog.value.setText(value)
        dialog.accept()

    QTimer.singleShot(0, configure)
    page.discover()
    assert len(calls) == 1 and calls[0][0] == "search"
    field = "endpoint_url" if key == "searxng" else "contact"
    assert calls[0][1]["search"][key][field] == value
    assert calls[0][1]["search"][key]["enabled"]


def test_query_preview_is_backend_deterministic(project):
    adapter = Adapter()
    data = project.config.model_dump(mode="json")
    data["search"]["query_expansion"] = {
        "enabled": True,
        "synonym_groups": [["room", "hall"]],
        "phrase_templates": ["empty {0}"],
        "max_expanded_queries": 5,
    }
    reply = adapter.execute(
        "preview_queries", project.root, {"configuration": data}, lambda e: None, lambda: False
    )
    assert reply.status == "succeeded"
    assert [q[1] for q in reply.value["queries"]] == [
        "synthetic fixture",
        "empty room",
        "empty hall",
    ]


@pytest.mark.parametrize("label", ["accepted", "maybe", "rejected"])
def test_native_review_persists_shared_history(qtbot, window, project, label):
    a = seed(project, "a")
    open_window(qtbot, window, project)
    window.navigate_candidate(a["candidate_id"])
    settle(qtbot, window)
    window.review.reviewer.setText("native-investigator")
    window.review.notes.setText("Native decision")
    window.review.review(label)
    settle(qtbot, window)
    evidence = window.review.inspector.current["evidence"]
    assert evidence["review"]["label"] == label
    assert evidence["review_events"][-1]["reviewer_email"] == "native-investigator"
    assert "Native decision" in window.review.inspector.evidence.toPlainText()


def test_gui_cannot_bypass_backend_review_identity(qtbot, window, project):
    a = seed(project, "a")
    open_window(qtbot, window, project)
    window.navigate_candidate(a["candidate_id"])
    settle(qtbot, window)
    window.review.review("accepted")
    settle(qtbot, window)
    assert "investigator identity" in window.operation.text()
    assert (
        workspace.inspect_candidate(project.root, a["candidate_id"])["evidence"]["review"]["label"]
        == "unrated"
    )


def test_backend_invalid_screen_policy_does_not_insert_defaults(project):
    result = services.run_stage(
        project,
        "screen-policy",
        model=None,
        low_threshold="",
        high_threshold="",
        policy=None,
        device="cpu",
    )
    assert result.status == "failed"
    assert project.config.screening.detectors == []


def test_explicit_policy_validated_by_shared_backend(project):
    result = run(
        project,
        "screen-policy",
        model="fasterrcnn_mobilenet_v3_large_320_fpn",
        low_threshold=0.23,
        high_threshold=0.78,
        policy="strict_zero_human",
        device="cpu",
    )
    policy = result["configuration"]["screening"]
    assert policy["detectors"][0]["low_threshold"] == 0.23
    assert policy["rules"][0]["outcomes"]["not_detected"] == "review_required"
    assert policy["rules"][0]["outcomes"]["detected"] == "excluded"


@pytest.mark.parametrize("selected_then_cleared", [False, True])
def test_unselected_screen_save_never_submits_blank_detector(
    qtbot, window, project, monkeypatch, selected_then_cleared
):
    open_window(qtbot, window, project)
    before = project.paths.config_snapshot_path.read_bytes()
    if selected_then_cleared:
        window.screen.model.setCurrentIndex(2)
        window.screen.low.setText("0.23")
        window.screen.model.setCurrentIndex(0)
    window.screen.policy.setCurrentIndex(1)
    monkeypatch.setattr(window, "submit", lambda *a, **kw: pytest.fail("Unselected detector must not be submitted"))
    window.screen.save_policy()
    assert "No detector selected" in window.operation.text()
    assert project.paths.config_snapshot_path.read_bytes() == before
    assert services.open_project(project.root).config.screening.detectors == []
    assert window.screen.model.currentData() is None
    assert window.screen.high.text() == ""


@pytest.mark.parametrize("low,high", [("", ""), ("", "0.78"), ("0.23", ""), ("  ", "0.78"), ("oops", "0.78"), ("0.9", "0.2"), ("nan", "0.78")])
def test_selected_detector_invalid_thresholds_do_not_submit(
    qtbot, window, project, monkeypatch, low, high
):
    open_window(qtbot, window, project)
    before = project.paths.config_snapshot_path.read_bytes()
    window.screen.model.setCurrentIndex(2)
    window.screen.low.setText(low)
    window.screen.high.setText(high)
    window.screen.policy.setCurrentIndex(1)
    monkeypatch.setattr(window, "submit", lambda *a, **kw: pytest.fail("Invalid thresholds must not be submitted"))
    window.screen.save_policy()
    assert "saved" in window.operation.text().lower()
    assert window.screen.low.text() == low
    assert window.screen.high.text() == high
    assert project.paths.config_snapshot_path.read_bytes() == before


@pytest.mark.parametrize("model_index", [1, 2])
def test_gui_explicit_detector_saves_and_reopens(qtbot, window, project, model_index):
    open_window(qtbot, window, project)
    window.screen.model.setCurrentIndex(model_index)
    model = window.screen.model.currentData()
    window.screen.low.setText("0.23")
    window.screen.high.setText("0.78")
    window.screen.policy.setCurrentIndex(1)
    window.screen.save_policy()
    settle(qtbot, window)
    saved = services.open_project(project.root).config.screening
    assert len(saved.detectors) == 1
    assert (saved.detectors[0].model, saved.detectors[0].low_threshold, saved.detectors[0].high_threshold) == (model, 0.23, 0.78)
    assert saved.rules[0].id == "strict_zero_human"
    before = project.paths.config_snapshot_path.read_bytes()
    window.screen.model.setCurrentIndex(0)
    window.screen.low.clear()
    window.screen.high.clear()
    window.screen.save_policy()
    assert project.paths.config_snapshot_path.read_bytes() == before
    window.project.open_action("open")
    settle(qtbot, window)
    assert window.screen.model.currentData() == model
    assert window.screen.low.text() == "0.23"
    assert window.screen.high.text() == "0.78"


@pytest.mark.parametrize("saved_detector", [False, True])
@pytest.mark.parametrize("save_path", ["advanced", "discover", "generic"])
def test_unrelated_saves_preserve_screening_and_ignore_pending_controls(
    qtbot, window, project, monkeypatch, saved_detector, save_path
):
    import json

    if saved_detector:
        run(project, "screen-policy", model="fasterrcnn_mobilenet_v3_large_320_fpn",
            low_threshold=0.23, high_threshold=0.78, policy="review_all", device="cpu")
    open_window(qtbot, window, project)
    expected = project.config.screening.model_dump(mode="json")
    window.screen.model.setCurrentIndex(1)
    window.screen.low.clear()
    window.screen.high.clear()
    real = services.run_stage

    def stage(path, action, **kwargs):
        if action == "search":
            return services.OperationResult("test", action, "succeeded", {})
        return real(path, action, **kwargs)

    monkeypatch.setattr(services, "run_stage", stage)
    if save_path == "discover":
        window.discover.seeds.setPlainText("unchanged screening test")
        window.discover.discover()
    else:
        config_value = project.config.model_dump(mode="json")
        config_value["run_name"] = "Unrelated configuration edit"
        if save_path == "advanced":
            window.project.configuration.setPlainText(json.dumps(config_value))
            window.project.save_advanced()
        else:
            window.submit("configure", {"configuration": config_value})
    settle(qtbot, window)
    assert services.open_project(project.root).config.screening.model_dump(mode="json") == expected
    window.project.open_action("open")
    settle(qtbot, window)
    assert window.session.state.config["screening"] == expected
    if not saved_detector:
        assert window.screen.model.currentData() is None
        assert window.screen.low.text() == window.screen.high.text() == ""


@pytest.mark.parametrize("key,value", [("searxng", "https://example.test/search"), ("commons", "https://example.test/project")])
@pytest.mark.parametrize("accept", [False, True])
def test_provider_setup_never_serializes_pending_screen_fields(
    qtbot, window, project, monkeypatch, key, value, accept
):
    from stimulus_harvester.gui.pages import ProviderSetupDialog

    open_window(qtbot, window, project)
    before = project.paths.config_snapshot_path.read_bytes()
    window.screen.model.setCurrentIndex(2)
    window.screen.low.clear()
    window.screen.high.clear()

    def dialog_exec(dialog):
        dialog.value.setText(value)
        return dialog.DialogCode.Accepted if accept else dialog.DialogCode.Rejected

    monkeypatch.setattr(ProviderSetupDialog, "exec", dialog_exec)
    window.discover.providers[key].click()
    assert window.discover.providers[key].isChecked() == accept
    assert project.paths.config_snapshot_path.read_bytes() == before
    configuration = window.discover.configuration()
    assert configuration["screening"]["detectors"] == []
    window.submit("configure", {"configuration": configuration})
    settle(qtbot, window)
    assert services.open_project(project.root).config.screening.detectors == []


def test_advanced_blank_detector_remains_strictly_rejected(qtbot, window, project):
    import json

    open_window(qtbot, window, project)
    before = project.paths.config_snapshot_path.read_bytes()
    value = project.config.model_dump(mode="json")
    value["screening"]["detectors"] = [{"id": "blank", "property": "human_presence", "model": None, "low_threshold": "", "high_threshold": ""}]
    window.project.configuration.setPlainText(json.dumps(value))
    window.project.save_advanced()
    settle(qtbot, window)
    assert "validation errors" in window.operation.text()
    assert "ValidationError" in window.log.toPlainText()
    assert not window.details_toggle.isChecked()
    assert project.paths.config_snapshot_path.read_bytes() == before


def test_freeze_blockers_navigate_to_candidate(qtbot, window, project):
    a = seed(project, "a", label="maybe")
    open_window(qtbot, window, project)
    window.review.reviewer.setText("native-investigator")
    panel = window.review.freeze
    panel.validate()
    settle(qtbot, window)
    assert not panel.create_button.isEnabled()
    assert panel.blockers.topLevelItemCount() == 1
    panel.navigate_blocker(panel.blockers.topLevelItem(0), 0)
    settle(qtbot, window)
    assert window.review.inspector.current["candidate_id"] == a["candidate_id"]


def test_confirmation_snapshot_rejects_concurrent_live_edit(project):
    seed(project, "a", label="accepted")
    prepared = workspace.freeze_preparation(project.root, request())
    run(
        project,
        "review",
        local_id="a",
        label="rejected",
        reviewer_email="later",
        notes="Concurrent browser-style edit",
    )
    result = services.run_stage(
        project,
        "freeze-create",
        request=request(),
        expected_snapshot_sha256=prepared["snapshot_sha256"],
    )
    assert result.status == "failed" and "changed since confirmation" in result.error["message"]
    assert services.list_frozen_artifacts(project) == []


def test_assembly_requires_freeze_and_readonly_view(window):
    assert not window.assemble.action_button.isEnabled()
    window.assemble.run_action()
    assert "Select an immutable freeze" in window.operation.text()
    assert window.assemble.manifest.isReadOnly() and window.export.manifest.isReadOnly()
    assert not any("Edit Freeze" in b.text() for b in window.findChildren(QPushButton))


def test_optional_dependency_only_disables_inference(qtbot, window, project):
    open_window(qtbot, window, project)
    window.dependencies = {"person_detector": {"available": False}}
    window.screen.refresh_capability()
    assert not window.screen.run_button.isEnabled()
    assert window.review.isEnabled() and window.export.isEnabled()


def test_worker_keeps_event_loop_live_and_delivers_progress(qtbot, window):
    class SlowAdapter:
        def execute(self, action, path, payload, progress, cancelled):
            progress({"stage": action, "status": "started"})
            time.sleep(0.2)
            return Reply(action, "succeeded", {"complete": True})

    window.session.adapter = SlowAdapter()
    beats = []
    timer = QTimer(window)
    timer.timeout.connect(lambda: beats.append(True))
    timer.start(10)
    window.submit("open", path="unused")
    qtbot.waitUntil(lambda: "cannot be interrupted" in window.operation.text(), timeout=1000)
    assert not window.cancel.isEnabled()
    window.close()
    assert window.isVisible()
    settle(qtbot, window)
    timer.stop()
    assert len(beats) >= 5


def test_cancel_before_start_does_not_invoke_backend(qtbot, monkeypatch):
    monkeypatch.setattr(services, "run_stage", lambda *a, **k: pytest.fail("must not run"))
    worker = Worker(Adapter(), "screen", "unused", {})
    worker.cancel_requested.set()
    with qtbot.waitSignal(worker.reply, timeout=3000) as result:
        worker.start()
    worker.wait(3000)
    assert result.args[0].status == "cancelled"


@pytest.mark.parametrize(
    "message,action,category",
    [
        ("ValueError: invalid input", "", "Input/configuration error"),
        ("DiscoveryFailure", "search", "Provider/setup failure"),
        ("No module named torch", "", "Dependency unavailable"),
        ("model weights failed", "screen", "Screening/model failure"),
        ("Freeze blocked: maybe", "", "Freeze blocker"),
        ("Immutable manifest integrity failure", "", "Integrity failure"),
        ("Project locked", "", "Recovery attention"),
        ("unexpected division", "", "Unexpected internal error"),
    ],
)
def test_actionable_error_classification(message, action, category):
    issue = classify(message, "technical traceback", action=action)
    assert issue.category == category and issue.guidance
    assert issue.details == "technical traceback"


def test_recovery_diagnostics_never_delete_evidence(project):
    services.list_frozen_artifacts(project)  # Initialize the additive registry before comparison.
    pending = project.root / "assemblies/.pending-test"
    pending.mkdir(parents=True)
    orphan = project.root / "freezes/orphan"
    orphan.mkdir(parents=True)
    lock = project.root / ".harvester.lock"
    lock.write_text("possibly active")
    before = inventory(project.root)
    state = workspace.workspace(project.root)
    assert {r["kind"] for r in state.recovery} == {
        "pending",
        "unregistered_artifact",
        "project_lock",
    }
    assert inventory(project.root) == before


def test_end_to_end_native_fixture(qtbot, window, tmp_path):
    from stimulus_harvester.gui.demo import exercise

    result = exercise(window, lambda: settle(qtbot, window), tmp_path / "desktop 研究 demo")
    assert result["old_freeze_unchanged"] and result["assembled_count"] == 2
    assert result["exported_count"] == 2 and result["artifact_history_count"] == 3


def test_saved_overlay_refuses_an_observation_of_another_asset(qtbot, window, tmp_path):
    from stimulus_harvester.frozen.demo import screen

    project = services.create_project(tmp_path / "overlay project", config())
    a = seed(project, "a", label="accepted")
    screen(project, {"a": 0.8})
    open_window(qtbot, window, project)
    window.navigate_candidate(a["candidate_id"])
    settle(qtbot, window)
    inspector = window.review.inspector
    inspector.boxes.setChecked(True)
    assert inspector.image.observation["asset_id"] == a["asset_id"]
    before = a["path"].read_bytes()
    observation = dict(inspector.observations.currentData())
    observation["asset_id"] = "sha256:another-asset"
    inspector.observations.setItemData(inspector.observations.currentIndex(), observation)
    inspector.observation_changed()
    assert inspector.image.observation is None
    assert "overlay disabled" in inspector.observation_summary.text()
    assert a["path"].read_bytes() == before


def test_screen_counts_match_backend_saved_observations(qtbot, window, tmp_path):
    project = workspace.create_desktop_fixture(tmp_path / "screen counts")
    open_window(qtbot, window, project)
    report = services.screening_report(project)
    assert "detected: 2" in window.screen.counts.text()
    assert "uncertain: 3" in window.screen.counts.text()
    assert window.session.state.screening["counts"] == report["counts"]


def test_configure_can_repair_invalid_yaml_and_preserve_artifacts(project):
    configuration = project.config.model_dump(mode="json")
    project.paths.config_snapshot_path.write_text("invalid: [yaml")
    result = services.run_stage(project.root, "configure", configuration=configuration)
    assert result.status == "succeeded"
    assert project.config.run_name == configuration["run_name"]


@pytest.mark.skipif(os.name != "nt", reason="Windows-only non-overwriting rename retry")
def test_transient_windows_publish_handle_is_retried(project, monkeypatch):
    from stimulus_harvester.frozen import store

    seed(project, "a", label="accepted")
    original = store.os.rename
    attempts = []

    def temporarily_busy(source, destination):
        attempts.append(str(destination))
        if len(attempts) < 3:
            raise PermissionError("temporary Windows indexer handle")
        return original(source, destination)

    monkeypatch.setattr(store.os, "rename", temporarily_busy)
    result = run(project, "freeze-create", request=request())
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert services.frozen_artifact(project, result["artifact_id"])["manifest"]["kind"] == "freeze"


def test_cli_native_review_and_config_parity(qtbot, window, project, tmp_path):
    from click.testing import CliRunner

    from stimulus_harvester.cli import cli
    from stimulus_harvester.config import config_to_yaml_str

    a = seed(project, "a")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "set-review",
            "--project",
            str(project.root),
            "--local-id",
            "a",
            "--label",
            "accepted",
            "--reviewer",
            "CLI investigator",
            "--notes",
            "Shared decision",
        ],
    )
    assert result.exit_code == 0, result.output
    open_window(qtbot, window, project)
    window.navigate_candidate(a["candidate_id"])
    settle(qtbot, window)
    assert window.review.inspector.current["evidence"]["review"]["label"] == "accepted"
    path = tmp_path / "updated.yaml"
    cfg = project.config.model_copy(update={"run_name": "CLI configured study"})
    path.write_text(config_to_yaml_str(cfg), encoding="utf-8")
    result = runner.invoke(
        cli, ["project", "configure", "--project", str(project.root), "--config", str(path)]
    )
    assert result.exit_code == 0, result.output
    window.submit("refresh")
    settle(qtbot, window)
    assert window.session.state.name == "CLI configured study"


def test_coverage_report_presented_without_raw_database_concepts(qtbot, window, project):
    seed(project, "a", label="accepted")
    open_window(qtbot, window, project)
    window.submit("diagnostics")
    settle(qtbot, window)
    assert "incremental URLs" in window.discover.query_preview.toPlainText()
    assert "Provider/query failures" in window.discover.query_preview.toPlainText()


def test_gui_download_recovery_uses_service_and_preserves_no_default(qtbot, window, project):
    import httpx
    import respx
    from stimulus_harvester.models import SearchResult
    from stimulus_harvester.review.db import init_db, upsert_image_from_search
    from PIL import Image
    import io

    url = 'https://images.example.org/recovery.png'
    conn = init_db(project.paths.db_path)
    upsert_image_from_search(conn, SearchResult(local_id='recovery', source_backend='openverse',
        search_query='q', seed_query='q', image_url=url, landing_page_url=url), project.root.name)
    conn.close()
    open_window(qtbot, window, project)
    assert '1 waiting to download' in window.discover.acquisition_summary.text()
    assert not window.discover.retry_button.isEnabled()
    with respx.mock:
        route = respx.get(url).mock(return_value=httpx.Response(403, text='Forbidden'))
        window.discover.download_pending()
        settle(qtbot, window)
        assert '1 unavailable' in window.discover.acquisition_summary.text()
        assert 'Access denied / request setup: 1' in window.discover.query_preview.toPlainText()
        assert '0 waiting to download' in window.discover.acquisition_summary.text()
        assert window.discover.retry_button.isEnabled()
        assert not window.details_toggle.isChecked()
        assert 'Coverage & failures' in window.operation.text()
        out = io.BytesIO()
        Image.new('RGB', (40,30), 'navy').save(out, format='PNG')
        route.mock(return_value=httpx.Response(200, content=out.getvalue(), headers={'Content-Type':'image/png'}))
        window.discover.retry_failed()
        settle(qtbot, window)
        assert '1 available' in window.discover.acquisition_summary.text()
        assert '0 unavailable' in window.discover.acquisition_summary.text()
        assert not window.discover.retry_button.isEnabled()
        assert route.call_count == 2
    conn = init_db(project.paths.db_path)
    assert [r[0] for r in conn.execute('SELECT status FROM acquisitions ORDER BY rowid')] == ['failed','success']
    conn.close()
    assert project.config.screening.detectors == []
    assert window.screen.model.currentData() is None and window.screen.low.text() == ''


def test_openverse_exhaustion_is_readable_without_opening_raw_details(qtbot, window, project):
    open_window(qtbot, window, project)
    result = {'pages_committed':12, 'source_occurrences':240, 'issues':[],
              'completions':[{'provider':'openverse','query':'prison cell',
                              'message':'Search limit reached for anonymous Openverse access'}]}
    window.completed(Reply('search','succeeded',result))
    assert 'Search limit reached' in window.discover.query_preview.toPlainText()
    assert '240' in window.discover.query_preview.toPlainText()
    assert not window.details_toggle.isChecked()
    assert window.discover.views.currentIndex() == 2


def test_download_contact_save_preserves_pending_queries_and_screen(qtbot, window, project, monkeypatch):
    from stimulus_harvester.models import SearchResult
    from stimulus_harvester.review.db import init_db, upsert_image_from_search
    conn=init_db(project.paths.db_path)
    upsert_image_from_search(conn,SearchResult(local_id='commons',source_backend='commons',
        search_query='old',seed_query='old',image_url='https://upload.wikimedia.org/a.png',landing_page_url='https://images.org/a'),project.root.name)
    conn.close()
    open_window(qtbot,window,project)
    window.discover.seeds.setPlainText('unsaved church\n研究')
    window.discover.contact.setText('https://example.org/contact')
    calls=[]
    monkeypatch.setattr(window,'configure_then',lambda action,configuration,**kwargs:calls.append((action,configuration)))
    window.discover.download_pending()
    assert calls[0][0]=='download'
    assert calls[0][1]['search']['seed_queries']==['unsaved church','研究']
    assert calls[0][1]['search']['commons']['contact']=='https://example.org/contact'
    assert calls[0][1]['screening']['detectors']==[]
    assert window.discover.seeds.toPlainText()=='unsaved church\n研究'


def test_acquisition_actions_do_not_run_during_provider_setup(window, monkeypatch):
    window.discover.provider_setup_dialog=object()
    monkeypatch.setattr(window.discover,'submit',lambda *a,**k:pytest.fail('No download during unresolved setup'))
    window.discover.download_pending()
    window.discover.retry_failed()
    window.discover.provider_setup_dialog=None


def test_browser_and_native_share_review_transition(project, monkeypatch):
    from fastapi.testclient import TestClient

    from stimulus_harvester.review import service

    seed(project, "a")
    events = []
    original = service.set_review_label

    def record(*args, **kwargs):
        events.append(kwargs["label"])
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "set_review_label", record)
    client = TestClient(services.review_application(project, reviewer_email="browser-investigator"))
    assert client.post("/images/a/label", json={"label": "maybe"}).status_code == 200
    run(project, "review", local_id="a", label="accepted", reviewer_email="native-investigator")
    assert events == ["maybe", "accepted"]


def test_safe_review_shortcut_records_decision(qtbot, window, project):
    from PySide6.QtCore import Qt

    a = seed(project, "a")
    open_window(qtbot, window, project)
    window.navigate_candidate(a["candidate_id"])
    settle(qtbot, window)
    window.review.reviewer.setText("keyboard-investigator")
    window.review.notes.setFocus()
    qtbot.keyClick(window.review.notes, Qt.Key.Key_M, Qt.KeyboardModifier.AltModifier)
    settle(qtbot, window)
    assert (
        workspace.inspect_candidate(project.root, a["candidate_id"])["evidence"]["review"]["label"]
        == "maybe"
    )


@pytest.mark.parametrize("mode", ["Light", "Dark"])
def test_live_theme_switch_keeps_text_contrast(window, mode):
    from PySide6.QtGui import QPalette

    window.project.appearance.setCurrentText(mode)
    for widget in (window.context, window.operation, window.project.path, window.export.manifest):
        role = (
            QPalette.ColorRole.Text
            if widget in (window.project.path, window.export.manifest)
            else QPalette.ColorRole.WindowText
        )
        background = (
            QPalette.ColorRole.Base
            if role == QPalette.ColorRole.Text
            else QPalette.ColorRole.Window
        )
        palette = widget.palette()

        def luminance(color):
            channels = [color.redF(), color.greenF(), color.blueF()]
            linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
            return sum(a * b for a, b in zip(linear, (0.2126, 0.7152, 0.0722)))

        foreground, base = luminance(palette.color(role)), luminance(palette.color(background))
        assert (max(foreground, base) + 0.05) / (min(foreground, base) + 0.05) >= 4.5
