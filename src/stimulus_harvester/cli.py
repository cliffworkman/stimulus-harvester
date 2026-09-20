"""CLI frontend for shared project services; legacy --run-id remains supported."""

from __future__ import annotations

import json
from pathlib import Path

import click

from stimulus_harvester import services
from stimulus_harvester.config import load_config
from stimulus_harvester.logging_utils import attach_run_log_file, configure_logging
from stimulus_harvester.paths import find_run_dir, new_run_id
from stimulus_harvester.storage import new_id


def _project(project_path: Path | None, run_id: str | None, *, frozen=False):
    if bool(project_path) == bool(run_id):
        raise click.UsageError("Specify exactly one of --project PATH or legacy --run-id ID")
    try:
        return services.open_project(
            project_path or find_run_dir(run_id), validate_config=not frozen
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc


def project_options(function):
    function = click.option("--run-id", help="Legacy lookup under ./runs; prefer --project.")(
        function
    )
    return click.option(
        "--project",
        "project_path",
        type=click.Path(path_type=Path),
        help="Explicit project or legacy run directory.",
    )(function)


def _run(project, stage: str, **options):
    attach_run_log_file(project.paths.log_path)
    try:
        result = services.run_stage(project, stage, **options)
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"operation-id: {result.operation_id}")
    if result.status == "partial":
        click.echo(
            "Operation completed partially; inspect discovery-report or screening-report for retained evidence.",
            err=True,
        )
    elif result.status != "succeeded":
        raise click.ClickException(f"{stage}: {result.status}: {result.error}")
    return result.result


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase log verbosity.")
def cli(verbose: int) -> None:
    """Local-first research stimulus discovery and human review."""
    configure_logging(verbose)


@cli.group("project")
def project_cmd():
    """Create, open/migrate, and inspect durable project folders."""


@project_cmd.command("create")
@click.option("--project", "project_path", required=True, type=click.Path(path_type=Path))
@click.option(
    "--config", "config_path", required=True, type=click.Path(exists=True, path_type=Path)
)
def create_cmd(project_path: Path, config_path: Path):
    try:
        project = services.create_project(project_path, load_config(config_path))
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(services.project_status(project), indent=2))


@project_cmd.command("open")
@project_options
def open_cmd(project_path: Path | None, run_id: str | None):
    """Open a project; legacy runs are backed up and migrated without discovery."""
    click.echo(json.dumps(services.project_status(_project(project_path, run_id)), indent=2))


@project_cmd.command("status")
@project_options
def status_cmd(project_path: Path | None, run_id: str | None):
    click.echo(json.dumps(services.project_status(_project(project_path, run_id)), indent=2))


@cli.command("discovery-report")
@project_options
@click.option(
    "--operation-id", default=None, help="Limit evidence and coverage to one search operation."
)
@click.option(
    "--provider-order",
    multiple=True,
    help="Repeated provider names define incremental coverage order.",
)
def discovery_report_cmd(project_path, run_id, operation_id, provider_order):
    """Print request/engine failures and descriptive provider overlap as JSON."""
    project = _project(project_path, run_id)
    try:
        report = services.discovery_diagnostics(
            project, operation_id=operation_id, provider_order=provider_order or None
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, indent=2))


@cli.command("search")
@click.option(
    "--config", "config_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)
)
@click.option("--run-name", default=None)
@click.option("--project", "project_path", type=click.Path(path_type=Path))
def search_cmd(config_path: Path | None, run_name: str | None, project_path: Path | None):
    """Discover into an existing project, or create one from --config."""
    try:
        if project_path and (project_path / "review" / "state.db").exists():
            if config_path or run_name:
                raise click.UsageError(
                    "Existing projects use their snapshot; omit --config/--run-name"
                )
            project = services.open_project(project_path)
        else:
            if config_path is None:
                raise click.UsageError("New projects require --config")
            config = load_config(config_path)
            if run_name:
                config.run_name = run_name
            root = project_path or config.output_root / (
                new_run_id(config.run_name) + "_" + new_id("run")[-8:]
            )
            project = services.create_project(root, config)
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = _run(project, "search")
    click.echo(
        f"run-id: {project.root.name}\nproject: {project.root}\nFound {summary['source_occurrences']} discovery record(s)."
    )


@cli.command("download")
@project_options
@click.option("--concurrency", type=int, default=None)
def download_cmd(project_path, run_id, concurrency):
    success, failed = _run(
        _project(project_path, run_id), "download", concurrency_override=concurrency
    )
    click.echo(f"Downloaded: {success} succeeded, {failed} failed.")


@cli.command("filter")
@project_options
def filter_cmd(project_path, run_id):
    passed, excluded = _run(_project(project_path, run_id), "filter")
    click.echo(f"Filter: {passed} passed, {excluded} excluded for non-semantic reasons.")


@cli.command("score")
@project_options
@click.option("--vlm/--no-vlm", default=None)
def score_cmd(project_path, run_id, vlm):
    """CLIP ranking, plus optional local VLM annotations; requires [clip]."""
    click.echo(json.dumps(_run(_project(project_path, run_id), "score", vlm=vlm)))


@cli.command("screen")
@project_options
@click.option(
    "--screening-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Screening-only YAML policy; saved with this operation.",
)
@click.option(
    "--reuse-raw",
    is_flag=True,
    help="Reinterpret compatible saved detections; no inference or downloads.",
)
def screen_cmd(project_path, run_id, screening_config, reuse_raw):
    import yaml
    from stimulus_harvester.screening.config import ScreeningConfig

    try:
        config = (
            ScreeningConfig.model_validate(
                yaml.safe_load(screening_config.read_text(encoding="utf-8"))
            ).model_dump(mode="json")
            if screening_config
            else None
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json.dumps(
            _run(
                _project(project_path, run_id),
                "screen",
                screening_config=config,
                reuse_raw=reuse_raw,
            ),
            indent=2,
        )
    )


@cli.command("screening-report")
@project_options
@click.option("--disposition", type=click.Choice(["eligible", "excluded", "review_required"]))
@click.option("--history/--no-history", default=True)
def screening_report_cmd(project_path, run_id, disposition, history):
    click.echo(
        json.dumps(
            services.screening_report(
                _project(project_path, run_id), disposition=disposition, include_history=history
            ),
            indent=2,
        )
    )


@cli.command("screening-override")
@project_options
@click.option(
    "--candidate", "candidate_id", required=True, help="Candidate UUID or primary local ID."
)
@click.option(
    "--disposition", required=True, type=click.Choice(["eligible", "excluded", "review_required"])
)
@click.option("--reviewer", required=True)
@click.option("--note", default=None)
def screening_override_cmd(project_path, run_id, candidate_id, disposition, reviewer, note):
    click.echo(
        json.dumps(
            _run(
                _project(project_path, run_id),
                "screen-override",
                candidate_id=candidate_id,
                disposition=disposition,
                reviewer=reviewer,
                note=note,
            ),
            indent=2,
        )
    )


@cli.command("review")
@project_options
@click.option("--host", default=None)
@click.option("--port", type=int, default=None)
@click.option("--reviewer", "reviewer_email", default=None, envvar="REVIEWER_EMAIL")
def review_cmd(project_path, run_id, host, port, reviewer_email):
    import uvicorn

    project = _project(project_path, run_id)
    app = services.review_application(project, reviewer_email=reviewer_email)
    host = host or project.config.review.host
    port = port or project.config.review.port
    click.echo(f"Review app: http://{host}:{port}/images")
    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command("export")
@project_options
@click.option(
    "--freeze",
    "freeze_id",
    help="Export immutable evidence from this freeze; creates an assembly if needed.",
)
@click.option("--assembly", "assembly_id", help="Export an existing immutable assembly.")
@click.option(
    "--label", type=click.Choice(["accepted", "maybe", "rejected", "all"]), default="accepted"
)
@click.option("--license-filter", type=click.Choice(["open_only", "all"]), default="all")
@click.option(
    "--formats",
    multiple=True,
    type=click.Choice(["csv", "jsonl", "html", "summary"]),
    default=("csv", "jsonl", "html", "summary"),
)
def export_cmd(project_path, run_id, label, license_filter, formats, freeze_id, assembly_id):
    if freeze_id or assembly_id:
        if (
            label != "accepted"
            or license_filter != "all"
            or set(formats) != {"csv", "jsonl", "html", "summary"}
        ):
            raise click.UsageError(
                "Frozen exports use complete frozen membership, all source claims, and all package formats"
            )
        click.echo(
            json.dumps(
                _run(
                    _project(project_path, run_id, frozen=True),
                    "export-frozen",
                    freeze_id=freeze_id,
                    assembly_id=assembly_id,
                ),
                indent=2,
            )
        )
        return
    result = _run(
        _project(project_path, run_id),
        "export",
        label=label,
        license_filter=license_filter,
        formats=formats,
    )
    for name, path in result.items():
        click.echo(f"{name}: {path}")
    click.echo(
        "This is a mutable legacy export. For a research package use export --freeze ID or --assembly ID."
    )


@cli.group("freeze")
def freeze_cmd():
    """Validate and capture immutable scientific review snapshots."""


@freeze_cmd.command("validate")
@project_options
@click.option(
    "--request",
    "request_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def freeze_validate_cmd(project_path, run_id, request_path):
    try:
        report = services.freeze_validate(
            _project(project_path, run_id),
            request=json.loads(request_path.read_text(encoding="utf-8")),
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(report, indent=2))
    if not report["valid"]:
        raise click.ClickException(
            "Freeze validation found blockers; see the complete report above"
        )


@freeze_cmd.command("create")
@project_options
@click.option(
    "--request",
    "request_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def freeze_create_cmd(project_path, run_id, request_path):
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        json.dumps(_run(_project(project_path, run_id), "freeze-create", request=request), indent=2)
    )


@freeze_cmd.command("list")
@project_options
def frozen_list_cmd(project_path, run_id):
    click.echo(
        json.dumps(
            services.list_frozen_artifacts(_project(project_path, run_id, frozen=True)), indent=2
        )
    )


@freeze_cmd.command("show")
@project_options
@click.option("--id", "artifact_id", required=True)
def frozen_show_cmd(project_path, run_id, artifact_id):
    try:
        result = services.frozen_artifact(_project(project_path, run_id, frozen=True), artifact_id)
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2))


@cli.command("assemble")
@project_options
@click.option("--freeze", "freeze_id", required=True)
@click.option("--parent-assembly", "parent_assembly_id")
def assemble_cmd(project_path, run_id, freeze_id, parent_assembly_id):
    click.echo(
        json.dumps(
            _run(
                _project(project_path, run_id, frozen=True),
                "assemble",
                freeze_id=freeze_id,
                parent_assembly_id=parent_assembly_id,
            ),
            indent=2,
        )
    )


@cli.command("verify-frozen")
@click.option(
    "--package",
    "package_path",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option("--sha256", "expected_sha256")
def verify_frozen_cmd(package_path, expected_sha256):
    """Verify a standalone frozen package, without a project or SQLite database."""
    try:
        result = services.verify_frozen_package(package_path, expected_sha256=expected_sha256)
    except (ValueError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(result, indent=2))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
