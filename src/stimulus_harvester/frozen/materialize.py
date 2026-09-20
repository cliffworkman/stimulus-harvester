"""Deterministic byte copies from a freeze, and portable frozen research packages."""

from __future__ import annotations

import csv
import html
import json

from stimulus_harvester.review.db import init_db
from . import store
from .snapshot import locate


def membership_hash(members):
    return store.sha(store.canonical(members))


def assemble(project, *, freeze_id, parent_assembly_id=None):
    conn = init_db(project.paths.db_path)
    try:
        store.ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        freeze, _ = store.load(conn, project, freeze_id, kind="freeze")
        previous = None
        if parent_assembly_id:
            previous, _ = store.load(conn, project, parent_assembly_id, kind="assembly")
            if previous["freeze_id"] != freeze_id:
                raise ValueError("Parent assembly must use the same freeze")
        membership = []
        sources = {}
        candidates = {c["candidate_id"]: c for c in freeze["candidates"]}
        for member in freeze["membership"]:
            asset = freeze["assets"][member["asset_id"]]
            if member["sha256"] != asset["sha256"] or not member["candidate_ids"]:
                raise ValueError("Frozen membership asset/identity mismatch")
            for cid in member["candidate_ids"]:
                c = candidates[cid]
                if (
                    c["asset_id"] != member["asset_id"]
                    or c["review"]["label"] != "accepted"
                    or c["screening"]["effective_disposition"] == "excluded"
                ):
                    raise ValueError(
                        "Frozen membership lacks accepted, non-excluded candidate evidence"
                    )
            sources[member["asset_id"]] = locate(project, asset)
            membership.append(
                {
                    **member,
                    "path": "stimuli/" + member["filename"],
                    "output_sha256": member["sha256"],
                    "materialization": "copied_original_bytes",
                    "original_locations": asset["locations"],
                }
            )
        fingerprint = membership_hash(membership)
        if previous and previous["membership_sha256"] != fingerprint:
            raise ValueError("Repeated assembly membership/hash differs from parent")
        manifest = {
            **store.metadata(
                project, "assembly", freeze_id=freeze_id, parent_id=parent_assembly_id
            ),
            "freeze_manifest_sha256": store.sha(store.canonical(freeze)),
            "membership": membership,
            "membership_sha256": fingerprint,
            "stimulus_count": len(membership),
            "software": store.software(project, conn),
            "reproduction": {
                "parent_assembly_id": parent_assembly_id,
                "verified_parent_bytes_and_membership_match": True if previous else None,
            },
        }
        with store.staging(project, "assembly") as stage:
            for member in membership:
                store.copy_verified(
                    sources[member["asset_id"]],
                    store.internal(stage, member["path"]),
                    member["sha256"],
                )
            return {
                **store.publish(conn, project, stage, manifest),
                "stimulus_count": len(membership),
                "membership_sha256": fingerprint,
                "reproduction": manifest["reproduction"],
            }
    finally:
        conn.rollback()
        conn.close()


def export_frozen(
    project,
    *,
    assembly_id=None,
    freeze_id=None,
    license_policy="preserve_all_source_claims_no_filter",
):
    if license_policy != "preserve_all_source_claims_no_filter":
        raise ValueError(
            "Frozen export supports only explicit preservation of all source claims; no legal/license eligibility inference"
        )
    if not assembly_id:
        if not freeze_id:
            raise ValueError("Frozen export requires an assembly ID or a freeze ID")
        assembly_id = assemble(project, freeze_id=freeze_id)["artifact_id"]
    conn = init_db(project.paths.db_path)
    try:
        store.ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        assembly, assembly_root = store.load(conn, project, assembly_id, kind="assembly")
        if freeze_id and freeze_id != assembly["freeze_id"]:
            raise ValueError("Assembly does not belong to requested freeze")
        freeze, freeze_root = store.load(conn, project, assembly["freeze_id"], kind="freeze")
        if assembly["freeze_manifest_sha256"] != store.sha(store.canonical(freeze)):
            raise ValueError("Assembly/freeze binding failed")
        claims = {
            s["source_id"]: s
            for c in freeze["candidates"] + freeze["related_candidates"]
            for s in c["sources"]
        }
        rows = [
            {**m, "source_claims": [claims[s] for s in m["source_ids"]]}
            for m in assembly["membership"]
        ]
        manifest = {
            **store.metadata(
                project, "frozen_export", freeze_id=assembly["freeze_id"], parent_id=assembly_id
            ),
            "assembly_id": assembly_id,
            "assembly_manifest_sha256": store.sha(store.canonical(assembly)),
            "freeze_manifest_sha256": assembly["freeze_manifest_sha256"],
            "license_policy": license_policy,
            "license_interpretation": "Source-specific claims, not legal conclusions or verified reuse permission.",
            "membership": rows,
            "stimulus_count": len(rows),
            "software": store.software(project, conn),
            "representations": {
                "images": "stimuli/",
                "csv": "stimuli.csv",
                "jsonl": "stimuli.jsonl",
                "html": "index.html",
                "summary": "summary.json",
                "freeze": "freeze/manifest.json",
                "assembly": "assembly-manifest.json",
            },
        }
        with store.staging(project, "frozen_export") as stage:
            for m in rows:
                store.copy_verified(
                    store.internal(assembly_root, m["path"]),
                    store.internal(stage, m["path"]),
                    m["sha256"],
                )
            # Use the already verified frozen inventory, never fresh hashes of live files.
            # This also catches evidence changes between loading and packaging a freeze.
            freeze_bytes = store.canonical(freeze)
            freeze_files = freeze["files"] + [
                {"path": "manifest.json", "sha256": store.sha(freeze_bytes)},
                {
                    "path": "checksums.sha256",
                    "sha256": store.sha(store.checksums(freeze, freeze_bytes)),
                },
            ]
            for file in freeze_files:
                store.copy_verified(
                    store.internal(freeze_root, file["path"]),
                    store.internal(stage, "freeze/" + file["path"]),
                    file["sha256"],
                )
            (stage / "assembly-manifest.json").write_bytes(store.canonical(assembly))
            (stage / "source-claims.json").write_bytes(store.canonical(claims))
            with (stage / "stimuli.jsonl").open("xb") as f:
                for row in rows:
                    f.write(store.canonical(row))
            fields = ["asset_id", "sha256", "path", "candidate_ids", "source_ids", "source_claims"]
            with (stage / "stimuli.csv").open("w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for row in rows:
                    writer.writerow(
                        {
                            k: json.dumps(row[k], ensure_ascii=False, sort_keys=True)
                            if isinstance(row[k], list)
                            else row[k]
                            for k in fields
                        }
                    )
            summary = {
                "freeze_id": freeze["freeze_id"],
                "assembly_id": assembly_id,
                "stimulus_count": len(rows),
                "asset_ids": [r["asset_id"] for r in rows],
                "candidate_ids": sorted({c for r in rows for c in r["candidate_ids"]}),
                "license_policy": license_policy,
                "scope_count": len(freeze["candidates"]),
                "frozen_review_counts": {
                    label: sum(c["review"]["label"] == label for c in freeze["candidates"])
                    for label in ["accepted", "rejected"]
                },
            }
            (stage / "summary.json").write_bytes(store.canonical(summary))
            cards = []
            for row in rows:
                claim_text = json.dumps(
                    [
                        {
                            "source_id": c["source_id"],
                            "provider": c["provider"],
                            "creator": c["creator"],
                            "license": c["license"],
                            "license_url": c["license_url"],
                            "source_page_url": c["source_page_url"],
                        }
                        for c in row["source_claims"]
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
                cards.append(
                    f'<article data-asset-id="{html.escape(row["asset_id"], quote=True)}"><img src="{html.escape(row["path"], quote=True)}" alt="Frozen stimulus"><p>{html.escape(row["filename"])}</p><pre>{html.escape(claim_text)}</pre></article>'
                )
            page = '<!doctype html><html lang="en"><meta charset="utf-8"><title>Frozen stimuli</title><style>body{font:16px system-ui;max-width:1100px;margin:30px auto}article{border-top:1px solid #aaa;padding:20px}img{max-width:100%;max-height:500px}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'
            page += f"<h1>Frozen stimuli</h1><p>{len(rows)} stimuli. Source claims are not verified reuse permission.</p>"
            page += '<p><a href="freeze/manifest.json">Freeze evidence</a> · <a href="assembly-manifest.json">Assembly evidence</a> · <a href="manifest.json">Export manifest</a></p>'
            page += "".join(cards) + "</html>"
            (stage / "index.html").write_text(page, encoding="utf-8")
            (stage / "README.txt").write_text(
                "Original image bytes are in stimuli/. Manifest files bind exact identities, human decisions, screening evidence, source claims and versions. The complete freeze is in freeze/. checksums.sha256 covers all package files except itself. Original external/project paths inside evidence are historical provenance; package inspection needs no SQLite database or original project. No license filter or image transformation was applied.\n",
                encoding="utf-8",
            )
            return {
                **store.publish(conn, project, stage, manifest),
                "assembly_id": assembly_id,
                "stimulus_count": len(rows),
            }
    finally:
        conn.rollback()
        conn.close()
