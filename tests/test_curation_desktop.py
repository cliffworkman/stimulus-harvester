import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton
from test_desktop import open_window, settle
from test_desktop import window as window_fixture

from stimulus_harvester.frozen.store import inventory
from stimulus_harvester.gui.components import CandidateBrowser
from stimulus_harvester.gui.curation_demo import create

window = window_fixture


@pytest.fixture
def demo(tmp_path):
    return create(tmp_path / "Curation 研究 demo")


def visible(browser):
    return [
        browser.table.item(i, 0).data(Qt.ItemDataRole.UserRole)
        for i in range(browser.table.rowCount())
        if not browser.table.isRowHidden(i)
    ]


def test_available_queue_filters_sort_and_no_scientific_mutation(qtbot, window, demo):
    open_window(qtbot, window, demo)
    browser = window.review.browser
    assert browser.availability.currentText() == "Available images"
    assert len(visible(browser)) == 4 and "4 visible / 8 total" in browser.counts.text()
    before = inventory(demo.root)
    browser.availability.setCurrentIndex(2)
    assert len(visible(browser)) == 4
    browser.availability.setCurrentIndex(0)
    browser.selectors["provider"].setCurrentIndex(browser.selectors["provider"].findData("commons"))
    assert len(visible(browser)) == 2
    browser.selectors["query"].setCurrentIndex(
        browser.selectors["query"].findData("church interior")
    )
    assert len(visible(browser)) == 1
    browser.selectors["license"].setCurrentIndex(browser.selectors["license"].findData("CC BY-SA"))
    assert len(visible(browser)) == 1
    for combo in browser.selectors.values():
        combo.setCurrentIndex(0)
    browser.selectors["review"].setCurrentIndex(browser.selectors["review"].findData("Maybe"))
    assert len(visible(browser)) == 1
    browser.selectors["review"].setCurrentIndex(0)
    browser.selectors["screening"].setCurrentIndex(
        browser.selectors["screening"].findData("Not run")
    )
    assert len(visible(browser)) == 5
    browser.selectors["screening"].setCurrentIndex(0)
    browser.min_width.setValue(41)
    assert len(visible(browser)) == 0
    browser.min_width.setValue(0)
    browser.table.sortItems(6, Qt.SortOrder.DescendingOrder)
    assert browser.table.item(0, 6).text() == "40"
    assert inventory(demo.root) == before


def test_numeric_sort_and_same_source_facets(qtbot):
    browser = CandidateBrowser()
    qtbot.addWidget(browser)
    rows = []
    for width in (2, 100, 30):
        rows.append(
            dict(
                candidate_id=str(width),
                title="Image " + str(width),
                sources=[
                    dict(provider="one", query="a", license="CC0"),
                    dict(provider="two", query="b", license="CC BY"),
                ],
                width=width,
                height=10,
                available=True,
                displayable=True,
                availability="Available",
                download_status="success",
                review_text="Unreviewed",
                screening_text="Not run",
            )
        )
    browser.populate(rows)
    browser.table.sortItems(6, Qt.SortOrder.AscendingOrder)
    assert [browser.table.item(i, 6).text() for i in range(3)] == ["2", "30", "100"]
    browser.selectors["provider"].setCurrentIndex(browser.selectors["provider"].findData("one"))
    browser.selectors["query"].setCurrentIndex(browser.selectors["query"].findData("b"))
    assert visible(browser) == []


def test_readable_inspector_history_raw_and_viewing_is_observational(qtbot, window, demo):
    open_window(qtbot, window, demo)
    before = inventory(demo.root)
    row = next(c for c in window.session.state.candidates if c["primary_local_id"] == "atrium")
    window.navigate_candidate(row["candidate_id"])
    settle(qtbot, window)
    panel = window.review.inspector
    assert (
        "Atrium study" in panel.identity.text() and row["candidate_id"] not in panel.identity.text()
    )
    text = panel.history.toPlainText()
    assert text.index("Accepted") < text.index("Maybe", text.index("Accepted"))
    assert "Current decision: Maybe" in text
    assert "Source claims are unverified" in panel.licenses.toPlainText()
    assert "Other exact-byte history" in panel.licenses.toPlainText()
    assert "CC BY-SA" in panel.licenses.toPlainText()
    assert "Multiple differing claims" in panel.licenses.toPlainText()
    assert panel.sources.topLevelItemCount() >= 3
    assert "fixture-investigator" in text and "Explicit later synthetic review" in text
    assert not panel.evidence.isVisible()
    panel.tabs.setCurrentIndex(panel.tabs.count() - 1)
    assert json.loads(panel.evidence.toPlainText())["candidate_id"] == row["candidate_id"]
    assert inventory(demo.root) == before


def test_missing_metadata_is_plain_and_screening_not_invented(qtbot, window, demo):
    open_window(qtbot, window, demo)
    row = next(c for c in window.session.state.candidates if c["primary_local_id"] == "vector")
    window.navigate_candidate(row["candidate_id"])
    settle(qtbot, window)
    panel = window.review.inspector
    assert "Not supplied" in panel.licenses.toPlainText()
    assert "Screening: Not run" in panel.identity.text()
    assert panel.sources.topLevelItem(0).child(0).text(1) == "Not supplied"
    assert "No recorded review decisions" in panel.history.toPlainText()


def test_filtering_away_selected_image_clears_review_target(qtbot, window, demo):
    open_window(qtbot, window, demo)
    row = next(c for c in window.session.state.candidates if c["primary_local_id"] == "atrium")
    window.navigate_candidate(row["candidate_id"])
    settle(qtbot, window)
    window.review.browser.filter.setText("nothing matches this")
    assert window.review.inspector.current is None
    assert window.review.inspector.rule_summary.text() == ""


@pytest.mark.parametrize("appearance", ["Light", "Dark"])
def test_small_window_keyboard_and_review_actions(qtbot, window, demo, appearance):
    open_window(qtbot, window, demo)
    window.resize(850, 690)
    window.set_appearance(appearance)
    window.tabs.setCurrentIndex(3)
    qtbot.wait(30)
    assert window.size().width() == 850 and window.size().height() == 690
    browser = window.review.browser
    browser.table.setFocus()
    qtbot.keyClick(browser.table, Qt.Key.Key_Down)
    settle(qtbot, window)
    assert window.review.inspector.current is not None
    actions = [
        b
        for b in window.review.findChildren(QPushButton)
        if b.text() in {"Accept", "Reject", "Maybe"}
    ]
    assert len(actions) == 3 and all(b.isVisible() and b.isEnabled() for b in actions)
    for action in actions:
        point = action.mapTo(window, action.rect().center())
        assert 0 <= point.y() < window.height(), (action.text(), point.y())
    assert not window.details_toggle.isChecked()


def test_failure_recovery_excludes_deterministic_cases(qtbot, window, demo, monkeypatch):
    open_window(qtbot, window, demo)
    calls = []
    monkeypatch.setattr(window, "submit", lambda action, payload: calls.append((action, payload)))
    window.discover.retry_failed()
    assert calls[0][0] == "retry-downloads"
    ids = calls[0][1]["candidate_ids"]
    assert len(ids) == 1
    assert (
        next(c for c in window.session.state.candidates if c["candidate_id"] == ids[0])[
            "primary_local_id"
        ]
        == "rate-limit"
    )


def test_corrupt_successful_file_hidden_without_mutating_record(qtbot, window, demo):
    image = demo.paths.images_original / "unreviewed-room.png"
    size = image.stat().st_size
    image.write_bytes(b"x" * size)
    open_window(qtbot, window, demo)
    row = next(
        c for c in window.session.state.candidates if c["primary_local_id"] == "unreviewed-room"
    )
    assert row["available"] and not row["displayable"]
    assert row["candidate_id"] not in visible(window.review.browser)
    assert row["download_status"] == "success"
    window.review.browser.availability.setCurrentIndex(2)
    assert row["candidate_id"] in visible(window.review.browser)


def test_filter_panel_does_not_resize_review_workspace(qtbot, window, demo):
    open_window(qtbot, window, demo)
    window.resize(850, 690)
    window.tabs.setCurrentIndex(3)
    browser = window.review.browser
    size = window.size()
    browser.filters_toggle.setChecked(True)
    qtbot.wait(20)
    assert browser.filter_dialog.isVisible() and window.size() == size
    browser.filter_dialog.accept()
    assert not browser.filters_toggle.isChecked()
