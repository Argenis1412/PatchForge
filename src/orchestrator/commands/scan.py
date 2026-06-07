"""V1 deterministic scan command.

Receives a pre-resolved :class:`~orchestrator.schemas.config.TargetConfig` from
:mod:`orchestrator.main` and runs :func:`~orchestrator.scanners.python.scan` to
produce ``findings.json``.  No AI client is imported or invoked.
"""

from __future__ import annotations

from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from orchestrator.git import repository_state
from orchestrator.observability.events import log_event, log_failure
from orchestrator.scanners.python import scan
from orchestrator.schemas.artifacts import RunMetadata, generate_run_id
from orchestrator.schemas.config import TargetConfig
from orchestrator.workspace import WorkspaceManager

console = Console()


def execute(config: TargetConfig) -> None:
    """Run the deterministic V1 scanner for *config* and persist findings.

    Writes ``findings.json`` and ``run.json`` unconditionally before any
    ``sys.exit`` / :class:`typer.Exit` so that callers can always inspect the
    results of an unsupported scan.

    Args:
        config: Fully resolved target configuration (workspace path already
            validated as external to the target repo).
    """
    console.print(
        Panel(
            f"[bold magenta]orchestrator Scanner (V1)[/bold magenta]\n"
            f"Target: [yellow]{config.target_path}[/yellow]",
            expand=False,
        )
    )

    # 1. Validate Git repository state
    try:
        repository_state(config.target_path)
    except ValueError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        raise typer.Exit(code=1)

    # 2. Setup workspace directories
    workspace_mgr = WorkspaceManager(config.workspace_path)
    workspace_mgr.setup()

    # 3. Generate run ID and directory
    run_id = generate_run_id()
    run_dir = workspace_mgr.create_run_directory(run_id)
    logs_dir = config.workspace_path / "logs"

    # 4. Log pipeline start
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scan",
        event="pipeline_start",
        data={"target": str(config.target_path)},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scan",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # 5. Run deterministic scanner
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"[green]Scanning {config.target_path}...", total=None)
        try:
            findings = scan(config.target_path, config.ignore_dirs)
            progress.update(task, completed=100)
        except Exception as exc:
            progress.update(task, completed=100)
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="scan",
                error_type="scanner_failed",
                message=str(exc),
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
            console.print(f"[bold red]Scanner failed: {exc}[/bold red]")
            raise typer.Exit(code=1)

    # 6. Build run metadata from scanner results
    now = datetime.now(timezone.utc)
    run_metadata = RunMetadata(
        run_id=run_id,
        target_path=str(config.target_path),
        workspace_path=str(config.workspace_path),
        base_commit=findings.base_commit,
        branch=findings.branch,
        status="scanned",
        created_at=now,
        updated_at=now,
        v1_supported=findings.v1_supported,
        support_reasons=findings.support_reasons,
        risk_budget="low",
        max_files=2,
        max_diff_lines=100,
    )

    # 7. Persist findings BEFORE any potential exit(1) — AC8
    # run.json must be written first: write_artifact calls
    # ensure_run_exists which requires run.json.
    workspace_mgr.write_run_json(run_id, run_metadata)
    workspace_mgr.write_artifact(run_id, "findings.json", findings.model_dump_json(indent=2))

    # 8. Log stage end
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scan",
        event="stage_end",
        data={
            "hotspots_count": len(findings.hotspots),
            "v1_supported": findings.v1_supported,
        },
        logs_dir=logs_dir,
        run_dir=run_dir,
    )
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scan",
        event="pipeline_end",
        data={"status": "scanned", "run_id": run_id},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # 9. Print summary
    console.print(
        Panel(
            f"[bold green]✔ Scanner completed successfully![/bold green]\n"
            f"Run ID: [yellow]{run_id}[/yellow]\n"
            f"Discovered [bold cyan]{len(findings.hotspots)}[/bold cyan] hotspots.\n"
            f"V1 supported: "
            f"{'yes' if findings.v1_supported else 'no'}\n"
            f"Artifacts stored in [cyan]{run_dir}[/cyan]",
            expand=False,
        )
    )

    # 10. Exit with code 1 AFTER writing findings for unsupported repos — AC4 / AC8
    if not findings.v1_supported:
        console.print("[bold red]V1 not supported. Reasons:[/bold red]")
        for reason in findings.unsupported_reasons:
            console.print(f"  [red]• {reason}[/red]")
        raise typer.Exit(code=1)
