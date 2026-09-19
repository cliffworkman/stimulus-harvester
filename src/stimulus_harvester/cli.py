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


def _project(project_path: Path | None, run_id: str | None):
    if bool(project_path) == bool(run_id):
        raise click.UsageError("Specify exactly one of --project PATH or legacy --run-id ID")
    try:
        return services.open_project(project_path or find_run_dir(run_id))
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
            "Discovery completed partially; successful pages are retained. Inspect discovery-report.",
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
    "--label", type=click.Choice(["accepted", "maybe", "rejected", "all"]), default="accepted"
)
@click.option("--license-filter", type=click.Choice(["open_only", "all"]), default="all")
@click.option(
    "--formats",
    multiple=True,
    type=click.Choice(["csv", "jsonl", "html", "summary"]),
    default=("csv", "jsonl", "html", "summary"),
)
def export_cmd(project_path, run_id, label, license_filter, formats):
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
        "Legacy accepted-folder copies are non-authoritative; no freeze/assembly exists yet."
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
