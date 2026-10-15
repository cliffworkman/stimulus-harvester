"""Criterion interactions through actual Qt controls and shared services."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog
from test_desktop import open_window, settle
from test_desktop import window as window_fixture
from test_generalized_screening import (
    activate,
    criterion,
    policy,
    report,
)
from test_generalized_screening import (
    project as project_fixture,
)

from stimulus_harvester import services
from stimulus_harvester.gui.criteria import CriterionDialog

window = window_fixture
project = project_fixture


def fill_dialog(text, *, manual=False, cancel=False):
    dialog = QApplication.activeModalWidget()
    assert isinstance(dialog, CriterionDialog)
    if cancel:
        dialog.reject()
        return
    dialog.text.setText(text)
    dialog.manual.setChecked(manual)
    dialog.confirmed.setChecked(True)
    dialog.accept_valid()


def test_add_cancel_edit_remove_without_inventing_detector(qtbot, window, project):
    open_window(qtbot, window, project)
    window.review.reviewer.setText("fixture-investigator")
    panel = window.screen.criteria
    QTimer.singleShot(30, lambda: fill_dialog("", cancel=True))
    panel.add()
    assert panel.items.topLevelItemCount() == 0
    QTimer.singleShot(30, lambda: fill_dialog("people"))
    panel.add()
    settle(qtbot, window)
    assert panel.items.topLevelItemCount() == 1
    assert services.open_project(project.root).config.screening.detectors == []
    assert window.screen.model.currentData() is None and window.screen.low.text() == ""
    panel.items.setCurrentItem(panel.items.topLevelItem(0))
    QTimer.singleShot(30, lambda: fill_dialog("fire exits", manual=True))
    panel.edit()
    settle(qtbot, window)
    assert "Human inspection" in panel.items.topLevelItem(0).text(1)
    assert (
        report(project)["candidates"][0]["evaluations"][0]["observation_status"]
        == "manual_review_required"
    )
    panel.items.setCurrentItem(panel.items.topLevelItem(0))
    panel.remove()
    settle(qtbot, window)
    assert panel.items.topLevelItemCount() == 0
    assert len(report(project)["policies"]) == 3


def test_interpretation_requires_confirmation_and_unknown_manual_choice(qtbot):
    dialog = CriterionDialog(None)
    qtbot.addWidget(dialog)
    dialog.text.setText("non-human animals")
    assert len(dialog.targets.selectedItems()) == 10
    assert "other animals" in dialog.explanation.text()
    dialog.accept_valid()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.confirmed.setChecked(True)
    assert dialog.value()["coverage"] == "partial"
    dialog.text.setText("religious symbols")
    dialog.confirmed.setChecked(True)
    dialog.accept_valid()
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.manual.setChecked(True)
    dialog.confirmed.setChecked(True)
    dialog.accept_valid()
    assert dialog.result() == QDialog.DialogCode.Accepted and dialog.value()["targets"] == []


def test_generic_advanced_settings_blank_rejected_valid_explicit_saved(qtbot, window, project):
    activate(project, policy(configured=False))
    open_window(qtbot, window, project)
    window.review.reviewer.setText("fixture-investigator")
    screen = window.screen
    screen.model.setCurrentIndex(2)
    screen.save_policy()
    assert services.open_project(project.root).config.screening.detectors == []
    screen.low.setText(".12")
    screen.high.setText(".64")
    screen.save_policy()
    settle(qtbot, window)
    config = services.open_project(project.root).config.screening
    assert (
        config.detectors[0].property == "coco_objects" and config.detectors[0].low_threshold == 0.12
    )
    assert config.criteria[0].targets == ["person"] and not config.rules


def test_multi_object_overlay_and_readable_criterion_reasons(qtbot, window, project):
    activate(project, policy(criterion(), criterion("cars")))
    services.run_stage(project.root, "screen")
    open_window(qtbot, window, project)
    item = window.session.state.candidates[0]
    window.tabs.setCurrentIndex(2)
    window.screen.views.setCurrentIndex(2)
    window.screen.browser.select_visible(item["candidate_id"])
    settle(qtbot, window)
    window.inspect(window.screen.inspector, item["candidate_id"])
    settle(qtbot, window)
    inspector = window.screen.inspector
    inspector.tabs.setCurrentIndex(2)
    assert inspector.boxes.isVisible()
    inspector.boxes.setChecked(True)
    assert {d["class"] for d in inspector.image.observation["raw"]["detections"]} == {
        "person",
        "dog",
        "car",
        "tv",
    }
    assert "cars: Match detected" in inspector.rule_summary.text()
    assert "Match detected" in item["screening_text"]
    before = (project.paths.images_original / "a.png").read_bytes()
    inspector.image.grab()
    assert (project.paths.images_original / "a.png").read_bytes() == before


def test_ordinary_criterion_entry_keyboard_and_small_window(qtbot, window, project):
    open_window(qtbot, window, project)
    window.tabs.setCurrentIndex(2)
    window.resize(850, 690)
    panel = window.screen.criteria
    assert window.screen.views.currentIndex() == 0 and panel.isVisible()
    panel.items.setFocus()
    qtbot.keyClick(panel.items, Qt.Key.Key_Tab)
    assert QApplication.focusWidget() is not None
    assert panel.width() <= window.width()
