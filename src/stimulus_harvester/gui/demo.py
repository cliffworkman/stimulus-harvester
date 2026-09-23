"""Scripted native widget workflow using only synthetic CP5 fixture assets.

Run: python -m stimulus_harvester.gui.demo --output NEW_DIRECTORY [--visible]
Confirmations are automated for this fixture, not investigator approvals.
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path


def exercise(window, wait, project_path, screenshots=None):
    from PySide6.QtWidgets import QPushButton

    def click(parent, prefix):
        target = next(b for b in parent.findChildren(QPushButton) if b.text().startswith(prefix))
        assert target.isEnabled(), prefix
        target.click()

    def capture(name, tab):
        if screenshots:
            from PySide6.QtTest import QTest

            window.tabs.setCurrentIndex(tab)
            QTest.qWait(100)
            window.grab().save(str(Path(screenshots) / (name + ".png")))

    window.project.path.setText(str(project_path))
    click(window.project, "Create synthetic demo")
    wait()
    assert window.session.state is not None, window.operation.text()
    assert len(window.session.state.candidates) == 6
    candidates = {c["primary_local_id"]: c["candidate_id"] for c in window.session.state.candidates}
    capture("01-project", 0)
    capture("02-discover", 1)
    window.discover.views.setCurrentIndex(1)
    capture("02b-candidates", 1)
    window.tabs.setCurrentIndex(2)
    window.screen.views.setCurrentIndex(1)
    window.screen.browser.select_candidate(candidates["overridden"])
    wait()
    window.screen.inspector.boxes.setChecked(True)
    obs = window.screen.inspector.observations.currentData()
    assert obs["raw"]["detections"] and window.screen.inspector.image.show_boxes
    capture("03-screen", 2)
    original = Path(project_path) / "images/original/overridden.png"
    # Resolve the path through the fixture's known recorded preview evidence, not a mutable selection.
    if not original.exists():
        original = next(Path(project_path).rglob("overridden.png"))
    original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
    window.review.reviewer.setText("desktop-fixture-investigator")
    window.navigate_candidate(candidates["accepted"])
    wait()
    window.review.notes.setText("Explicit human review of the review-required fixture")
    click(window.review, "Accept")
    wait()
    window.navigate_candidate(candidates["overridden"])
    wait()
    window.review.notes.setText("Explicit acceptance requires separate exclusion override")
    click(window.review, "Accept")
    wait()
    panel = window.review.freeze
    window.review.modes.setCurrentIndex(1)
    click(panel, "Validate freeze")
    wait()
    assert not panel.validation["valid"]
    assert "screening_excluded" in {b["code"] for b in panel.validation["blockers"]}
    window.review.modes.setCurrentIndex(0)
    window.review.disposition.setCurrentIndex(window.review.disposition.findData("review_required"))
    window.review.override_note.setText(
        "Synthetic adjudication: permit explicit investigator review"
    )
    click(window.review, "Record screening override")
    wait()
    window.review.modes.setCurrentIndex(1)
    click(panel, "Validate freeze")
    wait()
    assert panel.asset.count() == 2
    panel.asset.setCurrentIndex(1)
    panel.membership.setCurrentIndex(panel.membership.findData("include"))
    panel.reason.setText("Retain these accepted exact bytes once; preserve rejected source history")
    panel.deduplicate.setChecked(True)
    click(panel, "Record resolution")
    click(panel, "Validate freeze")
    wait()
    assert panel.validation["valid"], panel.validation
    capture("04-review-freeze", 3)
    click(panel, "Confirm & create freeze")
    wait()
    freeze = next(a for a in window.session.state.artifacts if a["kind"] == "freeze")
    freeze_path = Path(project_path) / freeze["relative_path"] / "manifest.json"
    old = freeze_path.read_bytes()
    for name in ("accepted", "duplicate"):
        window.navigate_candidate(candidates[name])
        wait()
        window.review.notes.setText("Later mutable decision; old freeze must remain unchanged")
        click(window.review, "Reject")
        wait()
    window.tabs.setCurrentIndex(4)
    window.assemble.freeze.setCurrentIndex(window.assemble.freeze.findData(freeze["artifact_id"]))
    click(window.assemble, "Assemble selected freeze")
    wait()
    assembly = next(a for a in window.session.state.artifacts if a["kind"] == "assembly")
    assert window.assemble.current_artifact["manifest"]["stimulus_count"] == 2
    capture("05-assemble-old-freeze", 4)
    window.tabs.setCurrentIndex(5)
    window.export.freeze.setCurrentIndex(window.export.freeze.findData(freeze["artifact_id"]))
    window.export.assembly.setCurrentIndex(window.export.assembly.findData(assembly["artifact_id"]))
    click(window.export, "Create frozen research package")
    wait()
    exported = next(a for a in window.session.state.artifacts if a["kind"] == "frozen_export")
    assert window.export.current_artifact["manifest"]["stimulus_count"] == 2
    assert freeze_path.read_bytes() == old
    assert hashlib.sha256(original.read_bytes()).hexdigest() == original_hash
    capture("06-export-history", 5)
    result = {
        "fixture": "Synthetic images and mocked saved detector outputs; scripted Qt interactions and confirmations",
        "project": str(project_path),
        "freeze": freeze,
        "assembly": assembly,
        "export": exported,
        "assembled_count": 2,
        "exported_count": 2,
        "artifact_history_count": len(window.session.state.artifacts),
        "old_freeze_unchanged": True,
        "original_image_unchanged_by_overlay": True,
        "verified_export_manifest": window.export.current_artifact["manifest"]["artifact_id"],
        "current_review_counts": window.session.state.status["reviews"],
        "frozen_review_counts": json.loads(old)["validation"]["scope_count"],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--visible", action="store_true")
    args = parser.parse_args()
    if not args.visible:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QEventLoop, QTimer
    from .window import Window

    args.output.mkdir(parents=True, exist_ok=False)
    app = QApplication.instance() or QApplication([])
    window = Window()
    window.show()
    window.confirm = lambda *args: True

    def wait():
        deadline = time.monotonic() + 90
        loop = QEventLoop()
        timer = QTimer()
        idle = []

        def check():
            if window.session.busy:
                idle.clear()
            else:
                idle.append(True)
            if len(idle) >= 4 or time.monotonic() > deadline:
                loop.quit()

        timer.timeout.connect(check)
        timer.start(30)
        loop.exec()
        timer.stop()
        if window.session.busy:
            raise TimeoutError(window.operation.text())

    wait()
    try:
        result = exercise(window, wait, args.output / "synthetic project 研究", args.output)
        window.project.appearance.setCurrentText("Dark")
        QTest.qWait(150)
        window.grab().save(str(args.output / "07-dark-export.png"))
        window.resize(850, 690)
        QTest.qWait(150)
        window.grab().save(str(args.output / "08-small-window.png"))
        result["platform"] = app.platformName()
        window.resize(1260, 940)
        window.project.appearance.setCurrentText("Light")
        QTest.qWait(150)
        window.grab().save(str(args.output / "09-light-export.png"))
        (args.output / "desktop-demo-results.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        wait()
        window.close()
        app.processEvents()


if __name__ == "__main__":
    main()
