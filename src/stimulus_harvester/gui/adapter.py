"""Small typed frontend boundary. Evidence payloads keep their backend schema."""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from pathlib import Path

from stimulus_harvester import services
from stimulus_harvester import workspace as backend
from stimulus_harvester.config import HarvesterConfig
from stimulus_harvester.search.query_expansion import expand_queries_from_config


@dataclass(frozen=True)
class Issue:
    category: str
    message: str
    guidance: str
    details: str


@dataclass(frozen=True)
class Reply:
    action: str
    status: str
    value: object = None
    workspace: backend.Workspace | None = None
    issue: Issue | None = None


def classify(message, details="", *, action=""):
    text = message.lower()
    if any(
        s in text
        for s in (
            "project locked",
            "project lock",
            "lock file",
            ".harvester.lock",
            "pending",
            "unregistered",
        )
    ):
        category, guidance = (
            "Recovery attention",
            "Another writer may be active. Preserve evidence and inspect Project recovery details; do not automatically delete files.",
        )
    elif any(
        s in text
        for s in (
            "hash",
            "integrity",
            "changed bytes",
            "missing frozen",
            "changed frozen",
            "while copying",
        )
    ):
        category, guidance = (
            "Integrity failure",
            "Restore the exact recorded bytes from a trusted backup. Do not edit a frozen manifest.",
        )
    elif any(s in text for s in ("torch", "module named", "dependency", "cuda unavailable")):
        category, guidance = (
            "Dependency unavailable",
            "Check optional capabilities in Project. Opening, review and export do not require ML dependencies.",
        )
    elif "freeze blocked" in text:
        category, guidance = (
            "Freeze blocker",
            "Run validation and resolve every listed candidate/duplicate problem.",
        )
    elif action == "search" or "discoveryfailure" in text:
        category, guidance = (
            "Provider/setup failure",
            "Inspect provider configuration and request diagnostics. Failed providers are not zero-result searches.",
        )
    elif action == "screen":
        category, guidance = (
            "Screening/model failure",
            "Inspect model/dependency details and saved error observations; model output is not ground truth.",
        )
    elif any(
        s in text
        for s in (
            "valueerror",
            "validation",
            "configuration",
            "invalid",
            "unknown",
            "required",
            "enter ",
            "must ",
            "specify",
        )
    ):
        category, guidance = (
            "Input/configuration error",
            "Correct the indicated input and retry. Existing artifacts remain unchanged.",
        )
    else:
        category, guidance = (
            "Unexpected internal error",
            "Preserve the technical details and operation evidence for diagnosis.",
        )
    return Issue(category, message, guidance, details or message)


class Adapter:
    """Methods execute in workers, never Qt callbacks. No independent scientific rules."""

    def execute(self, action, path, payload, progress, cancelled):
        if cancelled():
            return Reply(action, "cancelled", {"message": "Cancelled before start"})
        project_path = Path(path) if path else None
        try:
            value, status = None, "succeeded"
            if action in {"create", "demo"}:
                progress({"stage": action, "status": "started"})
                project = (
                    backend.create_desktop_fixture(project_path)
                    if action == "demo"
                    else services.create_project(
                        project_path, backend.default_configuration(payload["name"])
                    )
                )
                value = {"path": str(project.root), "project_id": project.project_id}
            elif action in {"open", "refresh"}:
                progress({"stage": action, "status": "started"})
            elif action == "dependencies":
                progress({"stage": action, "status": "started"})
                return Reply(action, status, backend.dependency_states(**payload))
            elif action == "inspect":
                progress({"stage": action, "status": "started"})
                return Reply(
                    action, status, backend.inspect_candidate(project_path, payload["candidate_id"])
                )
            elif action == "preview_queries":
                config = HarvesterConfig.model_validate(payload["configuration"])
                return Reply(
                    action,
                    status,
                    {
                        "queries": expand_queries_from_config(
                            config.search.query_expansion, config.search.seed_queries
                        ),
                        "providers": backend.provider_states(config),
                    },
                )
            elif action == "diagnostics":
                progress({"stage": action, "status": "started"})
                return Reply(action, status, services.discovery_diagnostics(project_path))
            elif action == "validate":
                progress({"stage": action, "status": "started"})
                value = backend.freeze_preparation(project_path, payload["request"])
            elif action == "artifact":
                progress({"stage": action, "status": "started"})
                value = services.frozen_artifact(project_path, payload["artifact_id"])
                return Reply(action, status, value)
            elif action == "verify":
                progress({"stage": action, "status": "started"})
                return Reply(action, status, services.verify_frozen_package(**payload))
            else:
                outcome = services.run_stage(
                    project_path, action, progress=progress, cancelled=cancelled, **payload
                )
                value, status = outcome.result, outcome.status
                if status == "failed":
                    message = json.dumps(outcome.error, ensure_ascii=False)
                    trace = project_path / "operations" / outcome.operation_id / "traceback.txt"
                    details = (
                        message + f"\nOperation: {outcome.operation_id}\nEvidence: {trace.parent}\n"
                    )
                    if trace.is_file():
                        details += trace.read_text(encoding="utf-8")
                    if value is not None:
                        details += "\nBackend result:\n" + json.dumps(
                            value, indent=2, ensure_ascii=False, default=str
                        )
                    return Reply(
                        action,
                        status,
                        value,
                        self._refresh(project_path),
                        classify(message, details, action=action),
                    )
            state = backend.workspace(project_path)
            return Reply(action, status, value, state)
        except Exception as exc:
            return Reply(
                action,
                "failed",
                issue=classify(
                    f"{type(exc).__name__}: {exc}", traceback.format_exc(), action=action
                ),
            )

    @staticmethod
    def _refresh(path):
        try:
            return backend.workspace(path)
        except Exception:
            return None
