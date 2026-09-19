"""Durable, page-at-a-time discovery across independent provider adapters."""

from __future__ import annotations

import json
import os
from pathlib import Path

from stimulus_harvester.config import HarvesterConfig, config_to_yaml_str
from stimulus_harvester.paths import RunPaths, run_paths_for, load_run_config
from stimulus_harvester.provenance import init_reproducibility_manifest, mark_stage_complete
from stimulus_harvester.review.db import init_db, upsert_image_from_search
from stimulus_harvester.search.commons import CommonsAdapter
from stimulus_harvester.search.evidence import Recorder, discovery_summary
from stimulus_harvester.search.openverse import OpenverseAdapter
from stimulus_harvester.search.query_expansion import expand_queries_from_config
from stimulus_harvester.search.searxng import SearxngAdapter
from stimulus_harvester.storage import ACTIVE_OPERATION, new_id, now, stored_path

PROVIDERS = {"openverse": OpenverseAdapter, "searxng": SearxngAdapter, "commons": CommonsAdapter}


def run_search_stage(
    config: HarvesterConfig,
    run_id: str,
    config_path: Path,
    *,
    project_root: Path | None = None,
    detailed: bool = False,
):
    paths = RunPaths(project_root) if project_root else run_paths_for(config.output_root, run_id)
    paths.ensure()
    if not paths.config_snapshot_path.exists():
        paths.config_snapshot_path.write_text(config_to_yaml_str(config), encoding="utf-8")
    else:
        config = load_run_config(paths.root)
    if not paths.reproducibility_path.exists():
        init_reproducibility_manifest(
            paths.reproducibility_path, run_id=run_id, config=config, config_path=config_path
        )
    conn = init_db(paths.db_path)
    own_operation = ACTIVE_OPERATION.get() is None
    operation_id = ACTIVE_OPERATION.get() or new_id("operation")
    if own_operation:
        conn.execute(
            "INSERT INTO operations VALUES(?,?,?,?)",
            (operation_id, "search", now(), json.dumps({"legacy_stage_call": True})),
        )
        conn.execute(
            "INSERT INTO operation_events(operation_id,occurred_at,status,details_json) VALUES(?,?,?,?)",
            (operation_id, now(), "started", "{}"),
        )
        conn.commit()
    token = ACTIVE_OPERATION.set(operation_id)
    try:
        recorder = Recorder(conn, paths.root, operation_id)
        raw_dir = paths.raw_api / operation_id
        expanded = expand_queries_from_config(
            config.search.query_expansion, config.search.seed_queries
        )
        enabled = [
            (name, cls, getattr(config.search, name))
            for name, cls in PROVIDERS.items()
            if getattr(config.search, name).enabled
        ]
        if not enabled:
            recorder(
                {
                    "provider": "configuration",
                    "status": "failed",
                    "error": {"kind": "setup_error", "message": "No discovery providers enabled"},
                }
            )
        if not expanded:
            recorder(
                {
                    "provider": "configuration",
                    "status": "failed",
                    "error": {"kind": "setup_error", "message": "No discovery queries configured"},
                }
            )
        with (paths.manifests / "search_results.jsonl").open("a", encoding="utf-8") as manifest:
            for name, cls, settings in enabled:
                try:
                    adapter = cls(settings)
                except Exception as exc:
                    recorder(
                        {
                            "provider": name,
                            "status": "failed",
                            "attempt": 0,
                            "error": {"kind": "setup_error", "message": str(exc)},
                        }
                    )
                    continue
                adapter.observe = recorder
                try:
                    for index, (seed, query) in enumerate(expanded, 1):
                        query_id = new_id("query")
                        lineage = {
                            "origin": "seed" if seed == query else "deterministic_expansion",
                            "seed_label": seed,
                            "query_order": index,
                            "recipe": config.search.query_expansion.model_dump(mode="json"),
                        }
                        context = {
                            "provider": name,
                            "query": query,
                            "seed_query": seed,
                            "query_id": query_id,
                            "query_lineage": lineage,
                        }
                        if adapter.unavailable:
                            recorder(
                                dict(
                                    context,
                                    status="query_deferred",
                                    reason="Provider access/rate/transport failure; retry in a later operation",
                                )
                            )
                            continue
                        adapter.query_id, adapter.query_lineage = query_id, lineage
                        recorder(dict(context, status="query_started"))
                        iterator = adapter.iter_pages(query, seed, raw_api_dir=raw_dir)
                        while True:
                            try:
                                page = next(iterator)
                            except StopIteration:
                                break
                            except OSError:
                                raise  # Disk/evidence errors must not be disguised as provider failures.
                            except Exception as exc:
                                recorder(
                                    dict(
                                        context,
                                        status="failed",
                                        error={"kind": "provider_error", "message": str(exc)},
                                    )
                                )
                                break
                            normalized = [
                                result.model_copy(
                                    update={
                                        "raw_response_path": stored_path(
                                            paths.root, result.raw_response_path, legacy=True
                                        )
                                    }
                                )
                                for result in page.results
                            ]
                            for result in normalized:
                                manifest.write(result.model_dump_json() + "\n")
                            manifest.flush()
                            os.fsync(manifest.fileno())
                            # Candidate/source changes and the committed-page event are one transaction.
                            event = dict(
                                page.evidence,
                                event_id=new_id("discovery_event"),
                                timestamp=now(),
                                status="page_committed",
                                result_count=len(normalized),
                            )
                            event, encoded = recorder.prepare(event, write=False)
                            with conn:
                                for result in normalized:
                                    upsert_image_from_search(conn, result, run_id, commit=False)
                                recorder.insert(event, encoded)
                            recorder.write_artifact(event, encoded)
                finally:
                    adapter.close()
        summary = discovery_summary(conn, operation_id)
        destination = paths.root / "operations" / operation_id / "discovery_summary.json"
        destination.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        if own_operation:
            conn.execute(
                "INSERT INTO operation_events(operation_id,occurred_at,status,details_json) VALUES(?,?,?,?)",
                (operation_id, now(), summary["discovery_status"], json.dumps(summary)),
            )
            conn.commit()
        mark_stage_complete(
            paths.reproducibility_path,
            "search"
            if summary["discovery_status"] == "succeeded"
            else "search_" + summary["discovery_status"],
        )
        return paths, summary if detailed else summary["source_occurrences"]
    finally:
        ACTIVE_OPERATION.reset(token)
        conn.close()
