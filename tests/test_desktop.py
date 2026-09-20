"""Qt/service boundary tests. Synthetic fixtures, no network or real ML inference."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest

pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPushButton

from stimulus_harvester import services, workspace
from stimulus_harvester.frozen.demo import seed, config, request, run
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
