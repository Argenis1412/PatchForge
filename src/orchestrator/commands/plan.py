"""Plan command: runs the Architect agent to produce an implementation plan."""

__all__ = [
    "execute",
]

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from orchestrator.agents import architect as architect_agent
from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.observability.events import log_event, log_failure
from orchestrator.risk import check_plan_gate
from orchestrator.schemas.config import TargetConfig, default_workspace_path
from orchestrator.schemas.findings import ScanFindings
from orchestrator.schemas.issue import parse_issue_markdown
from orchestrator.schemas.scout_output import ScoutOutput
from orchestrator.workspace import WorkspaceManager

console = Console()


def execute(
    run_id: str,
    workspace: Optional[Path] = None,
    env_file: Optional[Path] = None,
    issue_file: Optional[Path] = None,
) -> None:
    console.print(
        Panel(
            f"[bold cyan]PatchForge Architect (V1)[/bold cyan]\nRun ID: [yellow]{run_id}[/yellow]",
            expand=False,
        )
    )

    # 1. Resolve workspace path and ensure run exists
    if workspace is not None:
        workspace_path = Path(workspace).resolve()
    else:
        workspace_path = default_workspace_path(Path.cwd())

    workspace_mgr = WorkspaceManager(workspace_path)
    try:
        workspace_mgr.ensure_run_exists(run_id)
    except FileNotFoundError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # 2. Read metadata shared by both paths
    run_metadata = workspace_mgr.read_run_json(run_id)
    target_path = Path(run_metadata.target_path)
    logs_dir = workspace_path / "logs"
    run_dir = workspace_mgr.run_dir(run_id)

    # 3. Bootstrap target environment & load config (shared, before bifurcation)
    bootstrap_environment(env_file=env_file, target_path=target_path)
    try:
        config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
    except Exception as exc:
        console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="architect",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # 4. Architect input: issue file or Scout findings
    goal: str

    if issue_file is not None:
        # ── Issue file path ────────────────────────────────────────────────
        try:
            raw = Path(issue_file).read_text(encoding="utf-8")
        except FileNotFoundError:
            console.print(f"[bold red]Error: Issue file not found: {issue_file}[/bold red]")
            raise typer.Exit(code=1) from None

        try:
            issue_input = parse_issue_markdown(raw)
        except ValueError as exc:
            console.print(f"[bold red]Error: {exc}[/bold red]")
            raise typer.Exit(code=1) from None

        # Warn if findings.json already exists from a prior scan
        try:
            workspace_mgr.read_artifact(run_id, "findings.json")
            console.print(
                "[yellow]Warning: run has existing findings.json; "
                "issue file takes precedence.[/yellow]"
            )
        except Exception:
            pass

        workspace_mgr.write_artifact(run_id, "issue.md", raw)

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[green]Planning from issue...", total=None)
            try:
                output, meta = architect_agent.run_from_issue(
                    issue_input, config=config, trace_id=run_id, run_id=run_id
                )
                for plan_task in output.implementation_plan:
                    if plan_task.risk_level == "high":
                        plan_task.status = "blocked"
                progress.update(task, completed=100)
            except Exception as exc:
                progress.update(task, completed=100)
                log_failure(
                    trace_id=run_id,
                    run_id=run_id,
                    stage="architect",
                    error_type="architect_failed",
                    message=str(exc),
                    logs_dir=logs_dir,
                    run_dir=run_dir,
                )
                run_metadata.status = "failed"
                run_metadata.updated_at = datetime.now(timezone.utc)
                workspace_mgr.write_run_json(run_id, run_metadata)
                console.print(f"[bold red]Architect failed: {exc}[/bold red]")
                raise typer.Exit(code=1) from None

        goal = issue_input.title

    else:
        # ── Scout findings path (existing) ─────────────────────────────────
        try:
            findings_content = workspace_mgr.read_artifact(run_id, "findings.json")
        except Exception as exc:
            console.print(f"[bold red]Error reading findings: {exc}[/bold red]")
            raise typer.Exit(code=1) from None

        # Detect V1 deterministic scan findings.
        try:
            ScanFindings.model_validate_json(findings_content)
            console.print(
                "[bold red]Error: This run used V1 deterministic scan (no AI).[/bold red]"
            )
            console.print(
                "[red]`plan` requires AI-based analysis from the legacy Scout agent.[/red]"
            )
            console.print(
                "[red]Run `patchforge run .` to execute the full AI pipeline, "
                "or use `scan` for V1 deterministic results.[/red]"
            )
            run_metadata.status = "failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            workspace_mgr.write_run_json(run_id, run_metadata)
            raise typer.Exit(code=1) from None
        except typer.Exit:
            raise
        except ValidationError:
            pass

        try:
            scout_output = ScoutOutput.model_validate_json(findings_content)
        except Exception as exc:
            console.print(f"[bold red]Error reading findings: {exc}[/bold red]")
            raise typer.Exit(code=1) from None

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[green]Planning implementation steps...", total=None)
            try:
                output, meta = architect_agent.run(
                    scout_output, config=config, trace_id=run_id, run_id=run_id
                )
                for plan_task in output.implementation_plan:
                    if plan_task.risk_level == "high":
                        plan_task.status = "blocked"
                progress.update(task, completed=100)
            except Exception as exc:
                progress.update(task, completed=100)
                log_failure(
                    trace_id=run_id,
                    run_id=run_id,
                    stage="architect",
                    error_type="architect_failed",
                    message=str(exc),
                    logs_dir=logs_dir,
                    run_dir=run_dir,
                )
                run_metadata.status = "failed"
                run_metadata.updated_at = datetime.now(timezone.utc)
                workspace_mgr.write_run_json(run_id, run_metadata)
                console.print(f"[bold red]Architect failed: {exc}[/bold red]")
                raise typer.Exit(code=1) from None

        goal = scout_output.summary

    # 5. Check plan gate (shared)
    risk_result = check_plan_gate(run_metadata, output, workspace_mgr=workspace_mgr)
    if not risk_result.passed:
        console.print("[bold red]Plan blocked by risk gate:[/bold red]")
        for reason in risk_result.reasons:
            console.print(f"  - {reason}")
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="architect",
            error_type="risk_gate_blocked",
            message="; ".join(risk_result.reasons),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        raise typer.Exit(code=1) from None

    # 6. Write plan artifact (shared)
    workspace_mgr.write_artifact(run_id, "plan.json", output.model_dump_json(indent=2))

    # 6.5 Write experiment artifact (shared)
    from orchestrator.git import current_head, repository_identity
    from orchestrator.schemas.experiment import Experiment

    try:
        target_sha = current_head(target_path)
        repo_id = repository_identity(target_path)
        experiment = Experiment(
            run_id=run_id,
            plan=output,
            target_commit_sha=target_sha,
            repository_identity=repo_id,
            workspace_path=workspace_path,
        )
        workspace_mgr.write_experiment(run_id, experiment)
    except RuntimeError as exc:
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="architect",
            error_type="experiment_capture_failed",
            message=str(exc),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        console.print(f"[bold red]Error capturing experiment context: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # 7. Update run metadata (shared)
    files = set()
    for t in output.implementation_plan:
        files.update(t.files_to_modify)
    run_metadata.affected_files = sorted(files)
    run_metadata.goal = goal
    run_metadata.status = "planned"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="architect",
        event="stage_end",
        data={"cost_usd": meta.get("cost_usd"), "tasks_count": len(output.implementation_plan)},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    console.print(
        Panel(
            f"[bold green]✔ Plan generated successfully![/bold green]\n"
            f"Run ID: [yellow]{run_id}[/yellow]\n"
            f"Planned [bold cyan]{len(output.implementation_plan)}[/bold cyan] tasks"
            f" modifying [bold cyan]{len(run_metadata.affected_files)}[/bold cyan] files.\n"
            f"Plan saved to [cyan]{run_dir / 'plan.json'}[/cyan]",
            expand=False,
        )
    )
