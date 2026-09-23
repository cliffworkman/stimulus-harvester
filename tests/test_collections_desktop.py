"""Collection scope across native Qt services; no remote or ML dependence."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit, QPlainTextEdit
from test_collections import assign, make, seed, selection
from test_collections import project as project_fixture
from test_desktop import open_window, settle
from test_desktop import window as window_fixture

from stimulus_harvester import workspace

window = window_fixture
project = project_fixture


def choose(qtbot, window, cid):
    settle(qtbot, window)
    window.collections.selector.setCurrentIndex(window.collections.selector.findData(cid))
    settle(qtbot, window)


def inspect(qtbot, window, item):
    window.review.browser.select_visible(item["candidate_id"])
    window.inspect(window.review.inspector, item["candidate_id"])
    settle(qtbot, window)


def test_collection_ui_review_scope_and_freeze(qtbot, window, project):
    item = seed(project, "shared", label="maybe")
    a, b = make(project), make(project, "Church")
    assign(project, a, item)
    assign(project, b, item)
    open_window(qtbot, window, project)
    window.collections.actor.setText("fixture-investigator")
    choose(qtbot, window, a)
    assert window.session.state.collection_id == a
    assert window.discover.seeds.isReadOnly()
    assert "prison cell" in window.discover.seeds.toPlainText()
    assert window.screen.model.currentData() is None
    inspect(qtbot, window, item)
    assert "context only" in window.review.inspector.history.toPlainText()
    window.review.review("accepted")
    settle(qtbot, window)
    assert window.session.state.candidates[0]["label"] == "accepted"
    assert window.review.freeze.request()["scope"]["collection_id"] == a
    window.review.freeze.validate()
    settle(qtbot, window)
    assert window.review.freeze.validation["valid"]
    assert window.review.freeze.validation["summary"]["reviews"] == {"accepted": 1}
    choose(qtbot, window, b)
    assert window.session.state.candidates[0]["label"] == "unrated"
    inspect(qtbot, window, item)
    window.review.review("rejected")
    settle(qtbot, window)
    assert window.session.state.candidates[0]["label"] == "rejected"
    choose(qtbot, window, a)
    assert window.session.state.candidates[0]["label"] == "accepted"
    choose(qtbot, window, None)
    assert window.session.state.candidates[0]["label"] == "maybe"
    assert window.review.freeze.request()["scope"]["mode"] == "all_current"


def test_collection_selection_grid_table_marks_and_reopen(qtbot, window, project):
    a, b = seed(project, "a"), seed(project, "b")
    cid = make(project)
    assign(project, cid, a)
    open_window(qtbot, window, project)
    browser = window.review.browser
    browser.select_visible(b["candidate_id"])
    browser.mark(b["candidate_id"], True)
    choose(qtbot, window, cid)
    assert browser.visible_ids == [a["candidate_id"]]
    assert not browser.marked_ids
    assert window.review.inspector.current is None
    browser.view.setCurrentIndex(0)
    assert browser.visible_ids == [a["candidate_id"]]
    browser.view.setCurrentIndex(1)
    assert browser.visible_ids == [a["candidate_id"]]
    state = workspace.workspace(project.root, cid)
    assert state.collections[0]["found"] == 1 and state.collections[0]["reviewed"] == 0
    assert selection(project, cid, a)["event_id"] is None


def test_collection_policy_save_preserves_project_no_defaults(qtbot, window, project):
    cid = make(project)
    open_window(qtbot, window, project)
    choose(qtbot, window, cid)
    window.review.reviewer.setText("fixture")
    window.screen.save_policy()
    assert project.config.screening.detectors == []
    window.screen.model.setCurrentIndex(1)
    window.screen.low.setText("0.2")
    window.screen.high.setText("0.7")
    window.screen.policy.setCurrentIndex(1)
    window.screen.save_policy()
    settle(qtbot, window)
    assert (
        window.session.state.screening["policy"]["config"]["detectors"][0]["low_threshold"] == 0.2
    )
    assert project.config.screening.detectors == []
    window.screen.disable_screening()
    settle(qtbot, window)
    assert window.screen.model.currentData() is None
    assert project.config.screening.detectors == []


def test_collection_dialog_cancel_create_and_rename(qtbot, window, project):
    open_window(qtbot, window, project)
    QTimer.singleShot(20, lambda: QApplication.activeModalWidget().reject())
    window.collections.edit(new=True)
    assert window.session.state.collections == []

    def fill(name):
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        fields = dialog.findChildren(QLineEdit)
        fields[0].setText(name)
        fields[1].setText("fixture")
        dialog.findChild(QPlainTextEdit).setPlainText("prison cell\nprison interior")
        dialog.accept()

    QTimer.singleShot(20, lambda: fill("Prison"))
    window.collections.edit(new=True)
    settle(qtbot, window)
    cid = window.session.state.collections[0]["collection_id"]
    choose(qtbot, window, cid)
    QTimer.singleShot(20, lambda: fill("Renamed"))
    window.collections.edit(new=False)
    settle(qtbot, window)
    assert window.session.state.collection_id == cid
    assert window.session.state.status["collection"]["name"] == "Renamed"


def test_membership_actions_use_explicit_candidate_without_review(
    qtbot, window, project, monkeypatch
):
    item = seed(project, "one", label="accepted")
    cid = make(project)
    open_window(qtbot, window, project)
    window.review.reviewer.setText("fixture")
    window.tabs.setCurrentIndex(3)
    inspect(qtbot, window, item)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *args: (args[3][0], True))
    window.collections.change_membership(False)
    settle(qtbot, window)
    choose(qtbot, window, cid)
    assert window.session.state.candidates[0]["label"] == "unrated"
    inspect(qtbot, window, item)
    window.collections.change_membership(True)
    settle(qtbot, window)
    assert window.session.state.candidates == []
