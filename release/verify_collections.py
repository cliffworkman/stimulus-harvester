"""Native/package Arc B verification with synthetic evidence; never human acceptance.

Run with --runtime PATH_TO_PYTHON --work NEW_DIR --evidence NEW_DIR. The child
uses -I -B so a packaged runtime cannot borrow the source checkout or user packages.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess


def child(work, evidence):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton
    from stimulus_harvester import __version__, collections as col, services, workspace
    from stimulus_harvester.frozen.demo import config, seed, request, screen, strict_policy
    from stimulus_harvester.frozen.store import inventory
    from stimulus_harvester.gui.window import Window
    from stimulus_harvester.models import SearchResult
    from stimulus_harvester.review.db import init_db, upsert_image_from_search
    import time

    work.mkdir(parents=True, exist_ok=False)
    evidence.mkdir(parents=True, exist_ok=False)
    checks = []

    def passed(name, **data):
        checks.append(dict(name=name, passed=True, **data))
        (evidence / "collections-verification.json").write_text(
            json.dumps(
                dict(
                    version=__version__,
                    checks=checks,
                    passed=len(checks),
                    pytest_cases=0,
                    human_acceptance=False,
                    fresh_machine=False,
                ),
                indent=2,
            ),
            encoding="utf-8",
        )
        print("PASS", name, flush=True)

    p = services.create_project(work / "Synthetic collections 研究", config())
    shared = seed(p, "shared", label="accepted")
    legacy = services.run_stage(p, "freeze-create", request=request())
    assert legacy.status == "succeeded", legacy.error
    legacy_bytes = inventory(Path(legacy.result["path"]))

    def action(action, **kw):
        result = services.run_stage(
            p, "collection", action=action, actor="synthetic-investigator", **kw
        )
        assert result.status == "succeeded", result.error
        return result.result

    a = action("create", name="Prison interiors", phrases=["prison cell", "prison interior"])[
        "collection_id"
    ]
    b = action("create", name="Church interiors", phrases=["church interior", "chapel interior"])[
        "collection_id"
    ]
    for cid in (a, b):
        action("add", collection_id=cid, candidate_ids=[shared["candidate_id"]])
    assert workspace.workspace(p.root, a).candidates[0]["label"] == "unrated"
    passed("Additive migration and no inherited legacy acceptance")

    screen(p, {"shared": 0.9})  # Synthetic detector; no torch/model import or accuracy claim.
    alternate = strict_policy()
    alternate["rules"][0]["outcomes"]["detected"] = "review_required"
    action("policy", collection_id=a, configuration=strict_policy())
    action("policy", collection_id=b, configuration=alternate)
    sa, sb = workspace.workspace(p.root, a), workspace.workspace(p.root, b)
    ra, rb = sa.screening["candidates"][0], sb.screening["candidates"][0]
    assert ra["evaluations"][0]["observation_id"] == rb["evaluations"][0]["observation_id"]
    assert (
        ra["effective_disposition"] == "excluded"
        and rb["effective_disposition"] == "review_required"
    )
    action(
        "override",
        collection_id=a,
        candidate_id=shared["candidate_id"],
        expected_scope=ra["scope"],
        disposition="review_required",
        note="Explicit synthetic adjudication",
    )
    passed("Reusable observations with independent collection policies and override")

    app = QApplication([])
    w = Window()
    w.confirm = lambda *args: True
    w.show()

    def settle():
        for _ in range(4):
            deadline = time.monotonic() + 90
            while w.session.busy:
                QTest.qWait(20)
                assert time.monotonic() < deadline, "GUI worker timeout"
            QTest.qWait(40)

    def choose(cid):
        settle()
        w.collections.selector.setCurrentIndex(w.collections.selector.findData(cid))
        settle()
        assert w.session.state.collection_id == cid
        assert "configuration differs" not in w.warning.text()

    def inspect():
        w.review.browser.select_visible(shared["candidate_id"])
        settle()
        w.inspect(w.review.inspector, shared["candidate_id"])
        settle()

    settle()
    w.submit("open", path=str(p.root))
    settle()
    w.collections.actor.setText("synthetic-investigator")
    w.tabs.setCurrentIndex(3)
    freezes = []
    for cid, label in ((a, "accepted"), (b, "rejected")):
        choose(cid)
        inspect()
        w.review.review(label)
        settle()
        assert w.session.state.candidates[0]["label"] == label
        w.review.freeze.validate()
        settle()
        assert w.review.freeze.validation["valid"]
        w.review.freeze.create()
        settle()
        freeze_id = w.assemble.freeze.currentData()
        result = services.frozen_artifact(p, freeze_id)
        assert result["manifest"]["format_version"] == 2
        assert result["manifest"]["candidates"][0]["collection_review"]["label"] == label
        freezes.append(result)
    passed("Native collection Accept/Reject and GUI-confirmed v2 freezes")

    choose(a)
    inspect()
    geometry = []
    for appearance in ("Light", "Dark"):
        w.set_appearance(appearance)
        for width, height in ((850, 690), (1260, 940)):
            w.resize(width, height)
            QTest.qWait(200)
            browser = w.review.browser
            assert browser.grid.viewport().height() >= browser.grid.gridSize().height()
            for button in w.review.findChildren(QPushButton):
                if button.text() in {"Accept", "Maybe", "Reject"}:
                    assert w.rect().contains(button.mapTo(w, button.rect().center()))
            w.grab().save(str(evidence / f"{appearance.lower()}-{width}.png"))
            screen_rect = w.windowHandle().screen().availableGeometry()
            geometry.append(
                dict(
                    appearance=appearance,
                    requested=[width, height],
                    actual=[w.width(), w.height()],
                    fits_screen=w.frameGeometry().width() <= screen_rect.width()
                    and w.frameGeometry().height() <= screen_rect.height(),
                )
            )
    passed(
        "Native collection layout and visible review controls at 100% scaling", geometry=geometry
    )

    before = [inventory(Path(f["path"])) for f in freezes]
    w.review.review("rejected")
    settle()
    action("edit", collection_id=a, name="Renamed after freeze")
    for index, f in enumerate(freezes):
        assembled = services.run_stage(p, "assemble", freeze_id=f["manifest"]["artifact_id"])
        assert assembled.status == "succeeded", assembled.error
        exported = services.run_stage(
            p, "export-frozen", assembly_id=assembled.result["artifact_id"]
        )
        assert exported.status == "succeeded", exported.error
        assert services.verify_frozen_package(exported.result["path"])["valid"]
        assert inventory(Path(f["path"])) == before[index]
    assert inventory(Path(legacy.result["path"])) == legacy_bytes
    assert (
        services.run_stage(p, "assemble", freeze_id=legacy.result["artifact_id"]).status
        == "succeeded"
    )
    passed("Frozen v2 assembly/export after live edits; v1 reassembly preserved")

    # Persist realistic metadata scale, sharing canonical original bytes, with
    # pending/failed candidates and two provider/phrase histories. Fixture setup
    # SQL is explicitly synthetic, not a claim of successful remote acquisition.
    conn = init_db(p.paths.db_path)
    ids = []
    for i in range(1628):
        local = f"scale-{i:04}"
        upsert_image_from_search(
            conn,
            SearchResult(
                local_id=local,
                source_backend="synthetic-openverse" if i % 2 else "synthetic-commons",
                search_query="prison cell" if i % 3 else "chapel interior",
                seed_query="synthetic scale",
                image_url=f"https://example.invalid/{local}.png",
                landing_page_url="https://example.invalid/source",
                title=f"Synthetic scale image {i:04}",
                license="cc0" if i % 2 else "synthetic-claim",
            ),
            p.project_id,
            commit=False,
        )
        cid = conn.execute(
            "SELECT candidate_id FROM legacy_ids WHERE local_id=?", (local,)
        ).fetchone()[0]
        ids.append(cid)
        if i % 5:
            conn.execute(
                "UPDATE candidates SET asset_id=? WHERE candidate_id=?", (shared["asset_id"], cid)
            )
            conn.execute(
                "UPDATE images SET download_status='success',file_path=?,width=40,height=30,file_size_bytes=? WHERE local_id=?",
                (str(shared["path"].relative_to(p.root)), shared["path"].stat().st_size, local),
            )
        elif i % 10 == 0:
            conn.execute("UPDATE images SET download_status='failed' WHERE local_id=?", (local,))
    conn.commit()
    conn.close()
    start = time.perf_counter()
    action("add", collection_id=a, candidate_ids=ids[:1100])
    action("add", collection_id=b, candidate_ids=ids[700:])
    assignment_seconds = time.perf_counter() - start
    original_files = inventory(p.paths.images_original)
    start = time.perf_counter()
    w.submit("refresh")
    settle()
    refresh_seconds = time.perf_counter() - start
    assert w.session.state.status["curation"]["summary"]["found"] == 1101
    assert w.session.state.status["curation"]["summary"]["unique_images"] == 1
    for cid, found in ((b, 929), (a, 1101)):
        choose(cid)
        assert len(w.session.state.candidates) == found
        browser = w.review.browser
        visible = list(browser.visible_ids)
        browser.view.setCurrentIndex(0)
        browser.view.setCurrentIndex(1)
        assert browser.visible_ids == visible and browser.grid.count() <= 48
    assert inventory(p.paths.images_original) == original_files
    w.grab().save(str(evidence / "scale-collections.png"))
    passed(
        "Persisted 1628-row multi-collection scale, shared bytes and table/grid filtering",
        candidates=1629,
        assignment_seconds=assignment_seconds,
        refresh_seconds=refresh_seconds,
        current_collection_found=1101,
        grid_page_limit=48,
    )
    w.close()
    print(json.dumps({"passed": len(checks), "version": __version__}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    if args.child:
        child(args.work, args.evidence)
        return
    if not args.runtime:
        parser.error("--runtime is required")
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "windows"
    env["QT_SCALE_FACTOR"] = "1"
    env["PYTHONPATH"] = "C:\\nonexistent-poisoned-checkout"
    result = subprocess.run(
        [
            str(args.runtime),
            "-I",
            "-B",
            str(Path(__file__).resolve()),
            "--child",
            "--work",
            str(args.work),
            "--evidence",
            str(args.evidence),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / "process.log").write_text(
        result.stdout + "\n" + result.stderr, encoding="utf-8"
    )
    print(result.stdout)
    if result.returncode:
        raise RuntimeError(
            f"Collection verification failed; inspect {args.evidence / 'process.log'}"
        )


if __name__ == "__main__":
    main()
