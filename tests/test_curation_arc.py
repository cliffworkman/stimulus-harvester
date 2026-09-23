"""Curation arc: exact scope, rapid navigation, bounded display work and no implicit review."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import time

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox
from test_desktop import open_window, settle
from test_desktop import window as window_fixture

from stimulus_harvester import services, workspace
from stimulus_harvester.frozen.store import inventory
from stimulus_harvester.gui.components import CandidateBrowser
from stimulus_harvester.gui.curation_demo import create
from stimulus_harvester.review.db import init_db
from stimulus_harvester.review.service import record_batch

window = window_fixture


@pytest.fixture
def demo(tmp_path):
    return create(tmp_path / "Arc A 研究 spaces")


def selection(project):
    return [
        {k: r[k] for k in ("candidate_id", "asset_id", "label")}
        for r in workspace.workspace(project.root).candidates
        if r["displayable"]
    ]


def test_batch_exact_scope_history_and_superseding_decisions(demo):
    selected = selection(demo)[:2]
    conn = init_db(demo.paths.db_path)
    before = conn.execute("SELECT count(*) FROM review_events").fetchone()[0]
    conn.close()
    outcome = services.run_stage(
        demo,
        "review-batch",
        selection=selected,
        label="rejected",
        reviewer_email="fixture",
        notes="explicit batch",
    )
    assert outcome.status == "succeeded" and outcome.result["reviewed"] == 2
    after = workspace.workspace(demo.root)
    ids = {r["candidate_id"] for r in selected}
    assert all(r["label"] == "rejected" for r in after.candidates if r["candidate_id"] in ids)
    assert all(
        r["label"] == "unrated"
        for r in after.candidates
        if r["displayable"] and r["candidate_id"] not in ids
    )
    selected = [dict(r, label="rejected") for r in selected]
    assert (
        services.run_stage(
            demo, "review-batch", selection=selected, label="maybe", reviewer_email="fixture"
        ).status
        == "succeeded"
    )
    conn = init_db(demo.paths.db_path)
    assert conn.execute("SELECT count(*) FROM review_events").fetchone()[0] == before + 4
    assert (
        conn.execute("SELECT count(*) FROM review_events WHERE notes='explicit batch'").fetchone()[
            0
        ]
        == 2
    )
    conn.close()


@pytest.mark.parametrize(
    "problem",
    ["identity", "accepted", "empty", "duplicate", "stale_label", "stale_asset", "unknown"],
)
def test_batch_validation_is_all_or_nothing(demo, problem):
    selected = selection(demo)[:2]
    kwargs = dict(selection=selected, label="rejected", reviewer_email="fixture")
    if problem == "identity":
        kwargs["reviewer_email"] = " "
    if problem == "accepted":
        kwargs["label"] = "accepted"
    if problem == "empty":
        kwargs["selection"] = []
    if problem == "duplicate":
        kwargs["selection"] = [selected[0], selected[0]]
    if problem == "stale_label":
        selected[1]["label"] = "accepted"
    if problem == "stale_asset":
        selected[1]["asset_id"] = "different-bytes"
    if problem == "unknown":
        selected[1]["candidate_id"] = "unknown"
    conn = init_db(demo.paths.db_path)
    before = conn.execute("SELECT * FROM review_events").fetchall()
    with pytest.raises(ValueError):
        record_batch(conn, **kwargs)
    assert conn.execute("SELECT * FROM review_events").fetchall() == before
    conn.close()


def test_batch_database_failure_rolls_back_every_candidate(demo, monkeypatch):
    from stimulus_harvester.review import service

    selected = selection(demo)[:2]
    real = service.set_review_label
    calls = []

    def fail(conn, local, **kwargs):
        calls.append(local)
        if len(calls) == 2:
            raise RuntimeError("simulated storage failure")
        return real(conn, local, **kwargs)

    monkeypatch.setattr(service, "set_review_label", fail)
    conn = init_db(demo.paths.db_path)
    before = list(conn.execute("SELECT * FROM reviews"))
    events = list(conn.execute("SELECT * FROM review_events"))
    with pytest.raises(RuntimeError):
        record_batch(conn, selection=selected, label="rejected", reviewer_email="fixture")
    assert list(conn.execute("SELECT * FROM reviews")) == before
    assert list(conn.execute("SELECT * FROM review_events")) == events
    conn.close()


def large_rows(n=1628):
    return [
        dict(
            candidate_id=f"c{i:04}",
            title=f"Synthetic room {i:04}",
            sources=[dict(provider="fixture", query="room", license="CC0")],
            available=True,
            displayable=True,
            availability="Available",
            width=i + 1,
            height=40,
            review_text="Unreviewed",
            screening_text="Not run",
            shared_asset_candidates=2 if i % 3 == 0 else 1,
        )
        for i in range(n)
    ]


def test_large_grid_bounded_work_filter_sort_position_and_marks(qtbot):
    b = CandidateBrowser(available_only=True, triage=True)
    qtbot.addWidget(b)
    b.populate(large_rows())
    assert len(b.visible_ids) == 1628 and b.grid.count() == 48
    assert b.grid.pool.activeThreadCount() == 0  # Metadata-only rows never read image bytes.
    b.select_visible("c0047")
    b.move(1)
    assert b.candidate_id() == "c0048" and b.page == 1
    assert b.position.text() == "49 / 1,628"
    b.mark("c0048", True)
    b.view.setCurrentIndex(0)
    assert b.candidate_id() == "c0048"
    b.table.sortItems(6, Qt.SortOrder.DescendingOrder)
    b.filter_rows()
    assert b.visible_ids[0] == "c1627"
    assert b.candidate_id() == "c0048" and b.marked_ids == {"c0048"}
    b.filter.setText("room 000")
    assert len(b.visible_ids) == 10 and not b.marked_ids and b.candidate_id() is None
    assert "room 000" in b.counts.text()
    b.filter.setText("nothing matches")
    assert not b.visible_ids and "0 visible" in b.counts.text()


def test_active_zero_match_filter_survives_refresh(qtbot):
    b = CandidateBrowser()
    qtbot.addWidget(b)
    rows = large_rows(2)
    rows[0]["review_text"] = "Maybe"
    b.populate(rows)
    b.selectors["review"].setCurrentIndex(b.selectors["review"].findData("Maybe"))
    rows[0]["review_text"] = "Rejected"
    b.populate(rows)
    assert b.selectors["review"].currentData() == "Maybe" and not b.visible_ids


def test_grid_cache_is_bounded_and_selection_does_not_mark(qtbot):
    b = CandidateBrowser(triage=True)
    qtbot.addWidget(b)
    b.populate(large_rows(2))
    for n in range(300):
        b.grid.received((str(n), 1, 1), QImage())
    assert len(b.grid.cache) == b.grid.cache_limit
    b.grid.setCurrentRow(0)
    assert b.candidate_id() == "c0000" and not b.marked_ids
    b.grid.item(0).setCheckState(Qt.CheckState.Checked)
    assert b.marked_ids == {"c0000"}
    b.clear_marks()
    assert not b.marked_ids


def test_rapid_navigation_coalesces_and_never_displays_or_reviews_stale_image(
    qtbot, window, demo, monkeypatch
):
    open_window(qtbot, window, demo)
    b = window.review.browser
    original = window.session.adapter.execute
    inspected = []

    def slow(action, path, payload, progress, cancelled):
        if action == "inspect":
            inspected.append(payload["candidate_id"])
            time.sleep(0.15)
        return original(action, path, payload, progress, cancelled)

    monkeypatch.setattr(window.session.adapter, "execute", slow)
    before = inventory(demo.root)
    b.select_visible(b.visible_ids[0])
    assert window.review.isEnabled()
    for cid in b.visible_ids[1:]:
        b.select_visible(cid)
    assert window.review.inspector.current is None
    window.review.review("accepted")
    assert "Select and inspect" in window.operation.text()
    settle(qtbot, window)
    assert window.review.inspector.current["candidate_id"] == b.visible_ids[-1]
    assert inspected == [b.visible_ids[0], b.visible_ids[-1]]
    assert inventory(demo.root) == before


def test_filtering_during_pending_preview_does_not_resurrect_selection(qtbot, window, demo):
    open_window(qtbot, window, demo)
    b = window.review.browser
    b.select_visible(b.visible_ids[0])
    b.filter.setText("nothing matches")
    settle(qtbot, window)
    assert window.review.inspector.current is None and b.candidate_id() is None


@pytest.mark.parametrize("save", [False, True])
def test_batch_dialog_exact_scope_cancel_or_save(qtbot, window, demo, save):
    open_window(qtbot, window, demo)
    page = window.review
    page.reviewer.setText("fixture")
    cid = page.browser.visible_ids[0]
    page.browser.mark(cid, True)
    before = inventory(demo.root)

    def answer():
        dialog = next(d for d in page.findChildren(QDialog) if d.isVisible())
        combo = dialog.findChild(QComboBox)
        assert combo.currentData() is None
        if save:
            combo.setCurrentIndex(combo.findData("rejected"))
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        else:
            dialog.reject()

    QTimer.singleShot(20, answer)
    page.batch_review()
    settle(qtbot, window)
    if save:
        assert page.browser.rows[cid]["label"] == "rejected" and not page.browser.marked_ids
    else:
        assert inventory(demo.root) == before and page.browser.marked_ids == {cid}


def test_opt_in_advance_uses_prior_filtered_order(qtbot, window, demo):
    open_window(qtbot, window, demo)
    page = window.review
    assert not page.advance.isChecked()
    page.browser.select_visible(page.browser.visible_ids[0])
    settle(qtbot, window)
    next_id = page.browser.visible_ids[1]
    page.reviewer.setText("fixture")
    page.advance.setChecked(True)
    page.review("maybe")
    settle(qtbot, window)
    assert page.browser.candidate_id() == next_id
    assert page.inspector.current["candidate_id"] == next_id


def test_candidate_scoped_evidence_matches_full_report(demo):
    from stimulus_harvester.screening.engine import report_from_connection

    conn = init_db(demo.paths.db_path)
    full = report_from_connection(conn, demo)
    for row in full["candidates"]:
        cid = row["candidate_id"]
        scoped = report_from_connection(conn, demo, candidate_id=cid)
        for key in ("candidates", "observations", "evaluations", "overrides"):
            assert scoped[key] == [r for r in full[key] if r["candidate_id"] == cid]
    conn.close()


def test_shared_asset_filter_and_unknown_dimensions_are_explicit(qtbot):
    b = CandidateBrowser()
    qtbot.addWidget(b)
    rows = large_rows(6)
    rows[0]["width"] = None
    b.populate(rows)
    b.selectors["shared"].setCurrentIndex(b.selectors["shared"].findData("Multiple histories"))
    assert b.visible_ids == ["c0000", "c0003"]
    b.min_width.setValue(1)
    assert b.visible_ids == ["c0003"] and "unknown excluded" in b.counts.toolTip()


def test_thumbnail_paths_use_the_same_project_boundary(qtbot, tmp_path):
    b = CandidateBrowser(triage=True)
    qtbot.addWidget(b)
    rows = large_rows(1)
    rows[0]["file_path"] = "../outside.png"
    b.populate(rows, str(tmp_path))
    assert b.grid.item(0).data(Qt.ItemDataRole.UserRole + 1) is None
    assert b.grid.pool.activeThreadCount() == 0


def test_review_shortcuts_do_not_apply_from_freeze_mode(qtbot, window, demo):
    open_window(qtbot, window, demo)
    window.tabs.setCurrentIndex(3)
    page = window.review
    page.browser.select_visible(page.browser.visible_ids[0])
    settle(qtbot, window)
    before = inventory(demo.root)
    page.reviewer.setText('fixture')
    page.modes.setCurrentIndex(1)
    page.notes.setFocus()
    qtbot.keyClick(page.notes, Qt.Key.Key_R, Qt.KeyboardModifier.AltModifier)
    settle(qtbot, window)
    assert inventory(demo.root) == before
