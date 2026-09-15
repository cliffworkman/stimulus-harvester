"""Command-line entrypoint: search -> download -> filter -> score -> review -> export."""

from __future__ import annotations

from pathlib import Path

import click

from stimulus_harvester.config import load_config
from stimulus_harvester.logging_utils import attach_run_log_file, configure_logging
from stimulus_harvester.paths import RunPaths, find_run_dir, new_run_id


def _resolve_run_dir(run_id: str) -> Path:
    try:
        return find_run_dir(run_id)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


@click.group()
@click.option("-v", "--verbose", count=True, help="Increase log verbosity (-v for DEBUG).")
def cli(verbose: int) -> None:
    """Local-first stimulus image harvesting, filtering, scoring, and review pipeline.

    CLIP and VLM scores are review aids only -- they never automatically exclude a
    candidate. Final selection is always a human decision made via `review`.
    """
    configure_logging(verbose)


@cli.command("search")
@click.option(
    "--config", "config_path", required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to a YAML config file, e.g. configs/prison_inside_cell.yaml",
)
@click.option("--run-name", default=None, help="Override run_name from the config file.")
def search_cmd(config_path: Path, run_name: str | None) -> None:
    """Expand queries, run enabled search backends, and start a new run."""
    from stimulus_harvester.search import run_search_stage

    config = load_config(config_path)
    if run_name:
        config.run_name = run_name
    run_id = new_run_id(config.run_name)
    attach_run_log_file(RunPaths(root=config.output_root / run_id).log_path)

    try:
        _, total = run_search_stage(config, run_id, config_path)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"run-id: {run_id}")
    click.echo(f"Found {total} candidate(s).")
    click.echo(f"Next: python3 -m stimulus_harvester download --run-id {run_id}")


@cli.command("download")
@click.option("--run-id", required=True)
@click.option("--concurrency", type=int, default=None, help="Override download.concurrency from config.")
def download_cmd(run_id: str, concurrency: int | None) -> None:
    """Download candidate images. Every attempt (success or failure) is recorded."""
    from stimulus_harvester.download import run_download_stage

    run_dir = _resolve_run_dir(run_id)
    attach_run_log_file(RunPaths(root=run_dir).log_path)
    success, failed = run_download_stage(run_dir, concurrency_override=concurrency)
    click.echo(f"Downloaded: {success} succeeded, {failed} failed.")
    click.echo(f"Next: python3 -m stimulus_harvester filter --run-id {run_id}")


@cli.command("filter")
@click.option("--run-id", required=True)
def filter_cmd(run_id: str) -> None:
    """Apply non-semantic quality/duplicate filters only (never a relevance score)."""
    from stimulus_harvester.filter import run_filter_stage

    run_dir = _resolve_run_dir(run_id)
    attach_run_log_file(RunPaths(root=run_dir).log_path)
    passed, excluded = run_filter_stage(run_dir)
    click.echo(f"Filter: {passed} passed, {excluded} excluded for non-semantic reasons.")
    click.echo(f"Next: python3 -m stimulus_harvester score --run-id {run_id}")


@cli.command("score")
@click.option("--run-id", required=True)
@click.option(
    "--vlm/--no-vlm", "vlm", default=None,
    help="Force-enable/disable the VLM stage regardless of config (default: run it iff vlm.backend != noop).",
)
def score_cmd(run_id: str, vlm: bool | None) -> None:
    """Score candidates with CLIP (always) and optionally adjudicate with a VLM.

    Both are review aids: nothing here excludes a candidate from the run.
    """
    from stimulus_harvester.paths import load_run_config
    from stimulus_harvester.score import run_clip_scoring, run_vlm_scoring

    run_dir = _resolve_run_dir(run_id)
    attach_run_log_file(RunPaths(root=run_dir).log_path)

    try:
        scored = run_clip_scoring(run_dir)
    except ImportError as exc:
        raise click.ClickException(
            "CLIP scoring requires the optional [clip] extra (torch + open_clip_torch). "
            'Install a CUDA-matched torch wheel first, then: python3 -m pip install -e ".[clip]"\n'
            f"Original error: {exc}"
        ) from exc
    click.echo(f"CLIP-scored {scored} image(s).")

    config = load_run_config(run_dir)
    should_run_vlm = vlm if vlm is not None else (config.vlm.backend != "noop")
    if should_run_vlm:
        evaluated = run_vlm_scoring(run_dir)
        click.echo(f"VLM-adjudicated {evaluated} image(s) (backend={config.vlm.backend}).")
    else:
        click.echo("VLM stage skipped (vlm.backend=noop; pass --vlm to force it anyway).")

    click.echo(f"Next: python3 -m stimulus_harvester review --run-id {run_id}")


@cli.command("review")
@click.option("--run-id", required=True)
@click.option("--host", default=None, help="Override review.host from config.")
@click.option("--port", type=int, default=None, help="Override review.port from config.")
@click.option(
    "--reviewer", "reviewer_email", default=None, envvar="REVIEWER_EMAIL",
    help="Attribution stamp recorded on label/notes edits this session (or set REVIEWER_EMAIL).",
)
def review_cmd(run_id: str, host: str | None, port: int | None, reviewer_email: str | None) -> None:
    """Launch the local review app for this run."""
    import uvicorn

    from stimulus_harvester.paths import load_run_config
    from stimulus_harvester.review.app import create_app

    run_dir = _resolve_run_dir(run_id)
    config = load_run_config(run_dir)
    app = create_app(run_dir, config, reviewer_email=reviewer_email)
    bind_host = host or config.review.host
    bind_port = port or config.review.port
    click.echo(f"Review app: http://{bind_host}:{bind_port}/images")
    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info")


@cli.command("export")
@click.option("--run-id", required=True)
@click.option(
    "--label", type=click.Choice(["accepted", "maybe", "rejected", "all"]), default="accepted"
)
@click.option(
    "--license-filter", "license_filter", type=click.Choice(["open_only", "all"]), default="all",
    help="'open_only' keeps only images whose license is on the configured allowlist; "
    "unknown/restricted licenses are excluded from export (not from the working set).",
)
@click.option(
    "--formats", "formats", multiple=True,
    type=click.Choice(["csv", "jsonl", "html", "summary"]),
    default=("csv", "jsonl", "html", "summary"),
)
def export_cmd(run_id: str, label: str, license_filter: str, formats: tuple[str, ...]) -> None:
    """Export manifests, an accepted-images contact sheet, and a review summary."""
    from stimulus_harvester.export import export_run

    run_dir = _resolve_run_dir(run_id)
    attach_run_log_file(RunPaths(root=run_dir).log_path)
    written = export_run(run_dir, label=label, license_filter=license_filter, formats=tuple(formats))
    for fmt, path in written.items():
        click.echo(f"{fmt}: {path}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
