import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

# Force UTF-8 encoding for Rich progress bars on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
from rich.progress import Progress, SpinnerColumn, TextColumn

from orchestrator.agents.scout import run as run_scout
from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.pipeline import Pipeline
from orchestrator.schemas.config import TargetConfig
from orchestrator.workspace import WorkspaceManager

app = typer.Typer(help="orchestrator runtime - multi-stage software engineering workflows.")
console = Console()


def _load_target_config(
    path: Path,
    workspace: Optional[Path],
    env_file: Optional[Path],
) -> TargetConfig:
    bootstrap_environment(env_file=env_file, target_path=path)
    try:
        return TargetConfig.load(target_path=path, workspace_path=workspace)
    except ValueError as exc:
        console.print(f"[bold red]{exc}[/bold red]")
        raise typer.Exit(code=1) from exc


@app.command()
def doctor(
    path: Path = typer.Argument(..., help="Target project path"),
    json_output: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
):
    """Validate V1 repository readiness without modifying the target."""
    from orchestrator.doctor import check as doctor_check
    from orchestrator.schemas.doctor import CheckStatus

    result = doctor_check(path)

    if json_output:
        print(result.model_dump_json(indent=2))
    else:
        for check in result.checks:
            if check.status == CheckStatus.PASS:
                status_str = "[green]PASS[/green]"
            elif check.status == CheckStatus.FAIL:
                status_str = "[red]FAIL[/red]"
            else:
                status_str = "[yellow]WARN[/yellow]"
            console.print(f"  {status_str}  {check.message}")
            if check.detail:
                console.print(f"         {check.detail}")
            if check.fix_hint:
                console.print(f"         [dim]Hint: {check.fix_hint}[/dim]")

        is_dirty_str = "yes" if result.is_dirty else "no"
        console.print()
        if result.v1_supported:
            console.print(
                Panel(
                    f"[bold green]V1 supported[/bold green]\n"
                    f"  Target: [yellow]{result.target_path}[/yellow]\n"
                    f"  Branch: [cyan]{result.git_branch or 'N/A'}[/cyan]\n"
                    f"  Dirty:  [cyan]{is_dirty_str}[/cyan]",
                    expand=False,
                )
            )
        else:
            console.print(
                Panel(
                    f"[bold red]V1 not supported[/bold red]\n"
                    f"  Target: [yellow]{result.target_path}[/yellow]\n"
                    f"  Some required checks failed. See above for details.",
                    expand=False,
                )
            )

    if not result.v1_supported:
        raise typer.Exit(code=1)


@app.command()
def run(
    path: Path = typer.Argument(..., help="Target project path"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run the pipeline until the Executor stage, but don't apply changes",
    ),
    from_stage: Optional[str] = typer.Option(
        None, "--from-stage", help="Stage to resume from (scout, architect, executor)"
    ),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
):
    """Run the full orchestrator pipeline on a target project."""
    console.print(
        Panel(
            f"[bold cyan]orchestrator Pipeline[/bold cyan]\nTarget: [yellow]{path.absolute()}[/yellow]",
            expand=False,
        )
    )

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        progress.add_task("[cyan]Bootstrapping environment...", total=None)
        config = _load_target_config(path=path, workspace=workspace, env_file=env_file)

    pipeline = Pipeline(config=config, from_stage=from_stage)

    console.print("[bold blue]Starting pipeline execution...[/bold blue]")
    result = pipeline.execute(dry_run=dry_run)

    status = result.status
    if status == "completed":
        console.print(
            Panel(
                f"[bold green]Pipeline finished successfully![/bold green]\nRun ID: {result.run_id}",
                expand=False,
            )
        )
    elif status == "awaiting_review":
        console.print(
            Panel(
                f"[bold yellow]Pipeline finished with pending review items.[/bold yellow]\nRun ID: {result.run_id}",
                expand=False,
            )
        )
    else:
        console.print(
            Panel(
                f"[bold red]Pipeline failed ({status}).[/bold red]\nRun ID: {result.run_id}",
                expand=False,
            )
        )
        raise typer.Exit(code=1)


@app.command()
def scan(
    path: Path = typer.Argument(..., help="Target project path"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
):
    """Run only the Scout agent (reconnaissance) on a target project."""
    console.print(
        Panel(
            f"[bold magenta]orchestrator Scout (V1)[/bold magenta]\nTarget: [yellow]{path.absolute()}[/yellow]",
            expand=False,
        )
    )

    # Validate Git repository first
    from orchestrator.git import is_git_repo, repository_state
    try:
        git_state = repository_state(path)
    except ValueError as exc:
        console.print(f"[bold red]Error: {exc}[/bold red]")
        raise typer.Exit(code=1)

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        progress.add_task("[cyan]Bootstrapping environment...", total=None)
        config = _load_target_config(path=path, workspace=workspace, env_file=env_file)

    workspace_mgr = WorkspaceManager(config.workspace_path)
    workspace_mgr.setup()

    # Generate V1 Run ID and directory
    from datetime import datetime, timezone

    from orchestrator.schemas.artifacts import RunMetadata, generate_run_id

    run_id = generate_run_id()
    run_dir = workspace_mgr.create_run_directory(run_id)
    logs_dir = config.workspace_path / "logs"

    # Evaluate support
    reasons = []
    if config.capabilities.effective_supports_python:
        reasons.append("Python support detected")
    if config.capabilities.effective_supports_typescript:
        reasons.append("TypeScript support detected")
    if is_git_repo(path):
        reasons.append("Git repository verified")
    v1_supported = len(reasons) > 0

    run_metadata = RunMetadata(
        run_id=run_id,
        target_path=str(path.resolve()),
        workspace_path=str(config.workspace_path.resolve()),
        base_commit=git_state.head,
        branch=git_state.branch,
        status="scanning",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        v1_supported=v1_supported,
        support_reasons=reasons,
        risk_budget="medium",
        max_files=5,
        max_diff_lines=500,
    )
    workspace_mgr.write_run_json(run_id, run_metadata)

    # Log start event
    from orchestrator.observability.events import log_event
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scout",
        event="pipeline_start",
        data={"target": str(path.resolve())},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scout",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # Run Scout agent
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task(f"[green]Scanning {config.target_path}...", total=None)
        try:
            output, meta = run_scout(config, trace_id=run_id, run_id=run_id)
            progress.update(task, completed=100)
        except Exception as exc:
            progress.update(task, completed=100)
            from orchestrator.observability.events import log_failure
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="scout",
                error_type="scout_failed",
                message=str(exc),
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
            run_metadata.status = "failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            workspace_mgr.write_run_json(run_id, run_metadata)
            console.print(f"[bold red]Scout failed: {exc}[/bold red]")
            raise typer.Exit(code=1)

    # Write findings
    workspace_mgr.write_artifact(run_id, "findings.json", output.model_dump_json(indent=2))

    # Log end event
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scout",
        event="stage_end",
        data={"cost_usd": meta.get("cost_usd"), "hotspots_count": len(output.hotspots)},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="scout",
        event="pipeline_end",
        data={"status": "scanned", "run_id": run_id},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # Update metadata status to scanned
    run_metadata.status = "scanned"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    console.print(Panel(
        f"[bold green]✔ Scout completed successfully![/bold green]\n"
        f"Run ID: [yellow]{run_id}[/yellow]\n"
        f"Discovered [bold cyan]{len(output.hotspots)}[/bold cyan] findings.\n"
        f"Artifacts stored in [cyan]{run_dir}[/cyan]",
        expand=False,
    ))


@app.command()
def plan(
    run_id: str = typer.Argument(..., help="Run ID of an existing run"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
):
    """Run the Architect agent to generate an implementation plan for a run."""
    console.print(
        Panel(
            f"[bold cyan]orchestrator Architect (V1)[/bold cyan]\nRun ID: [yellow]{run_id}[/yellow]",
            expand=False,
        )
    )

    from datetime import datetime, timezone

    from orchestrator.agents.architect import run as run_architect
    from orchestrator.observability.events import log_event, log_failure
    from orchestrator.schemas.config import default_workspace_path
    from orchestrator.schemas.scout_output import ScoutOutput

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
        raise typer.Exit(code=1)

    # 2. Read metadata and findings
    run_metadata = workspace_mgr.read_run_json(run_id)
    target_path = Path(run_metadata.target_path)
    logs_dir = workspace_path / "logs"
    run_dir = workspace_mgr.run_dir(run_id)

    try:
        findings_content = workspace_mgr.read_artifact(run_id, "findings.json")
        scout_output = ScoutOutput.model_validate_json(findings_content)
    except Exception as exc:
        console.print(f"[bold red]Error reading findings: {exc}[/bold red]")
        raise typer.Exit(code=1)

    # 3. Bootstrap target environment & load config
    bootstrap_environment(env_file=env_file, target_path=target_path)
    try:
        config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
    except Exception as exc:
        console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
        raise typer.Exit(code=1)

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

    # 4. Run Architect agent
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("[green]Planning implementation steps...", total=None)
        try:
            output, meta = run_architect(scout_output, config=config, trace_id=run_id, run_id=run_id)
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
            raise typer.Exit(code=1)

    # 5. Write plan artifact
    workspace_mgr.write_artifact(run_id, "plan.json", output.model_dump_json(indent=2))

    # 6. Update run metadata
    files = set()
    for t in output.implementation_plan:
        files.update(t.files_to_modify)
    run_metadata.affected_files = sorted(list(files))
    run_metadata.goal = scout_output.summary
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

    console.print(Panel(
        f"[bold green]✔ Plan generated successfully![/bold green]\n"
        f"Run ID: [yellow]{run_id}[/yellow]\n"
        f"Planned [bold cyan]{len(output.implementation_plan)}[/bold cyan] tasks modifying [bold cyan]{len(run_metadata.affected_files)}[/bold cyan] files.\n"
        f"Plan saved to [cyan]{run_dir / 'plan.json'}[/cyan]",
        expand=False,
    ))


@app.command()
def preview(
    run_id: str = typer.Argument(..., help="Run ID of an existing run"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
):
    """Generate and validate a unified patch without modifying the target repository."""
    console.print(
        Panel(
            f"[bold yellow]orchestrator Preview & Validation (V1)[/bold yellow]\nRun ID: [yellow]{run_id}[/yellow]",
            expand=False,
        )
    )

    import hashlib
    from datetime import datetime, timezone

    from orchestrator.agents.executor import run as run_executor
    from orchestrator.observability.events import log_event, log_failure
    from orchestrator.schemas.architect_output import ArchitectOutput
    from orchestrator.schemas.config import default_workspace_path
    from orchestrator.schemas.validator_output import ValidatorOutput
    from orchestrator.validation_workspace import (
        apply_patch_to_copy,
        create_validation_workspace,
        run_validation_in_copy,
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
        raise typer.Exit(code=1)

    # 2. Read metadata and plan
    run_metadata = workspace_mgr.read_run_json(run_id)
    target_path = Path(run_metadata.target_path)
    logs_dir = workspace_path / "logs"
    run_dir = workspace_mgr.run_dir(run_id)

    try:
        plan_content = workspace_mgr.read_artifact(run_id, "plan.json")
        architect_output = ArchitectOutput.model_validate_json(plan_content)
    except Exception as exc:
        console.print(f"[bold red]Error reading implementation plan: {exc}[/bold red]")
        raise typer.Exit(code=1)

    # 3. Bootstrap target environment & load config
    bootstrap_environment(env_file=env_file, target_path=target_path)
    try:
        config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
    except Exception as exc:
        console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
        raise typer.Exit(code=1)

    # 4. Run Executor
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="executor",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    staging_dir = run_dir / "staging"
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("[green]Executing planned tasks and generating patch...", total=None)
        try:
            executor_output, exec_meta = run_executor(
                architect_output=architect_output,
                config=config,
                staging_dir=staging_dir
            )
            progress.update(task, completed=100)
        except Exception as exc:
            progress.update(task, completed=100)
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="executor",
                error_type="executor_failed",
                message=str(exc),
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
            run_metadata.status = "failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            workspace_mgr.write_run_json(run_id, run_metadata)
            console.print(f"[bold red]Executor failed: {exc}[/bold red]")
            raise typer.Exit(code=1)

    # 5. Consolidate file changes into a single patch.diff
    diffs = []
    for change in executor_output.applied + executor_output.pending_review:
        if change.diff:
            diffs.append(change.diff)
    patch_diff = "\n".join(diffs)
    workspace_mgr.write_artifact(run_id, "patch.diff", patch_diff)

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="executor",
        event="stage_end",
        data={"cost_usd": exec_meta.get("cost_usd"), "applied_count": len(executor_output.applied)},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # 6. Run Validator in temporary copy
    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="validator",
        event="stage_start",
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    patch_path = run_dir / "patch.diff"
    validator_output = None
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("[green]Validating patch in isolated workspace...", total=None)
        try:
            with create_validation_workspace(original_root=target_path, patch_path=patch_path) as val_ws:
                apply_res = apply_patch_to_copy(val_ws.temporary_root, val_ws.patch_path)
                if apply_res.return_code != 0:
                    validator_output = ValidatorOutput(
                        overall_passed=False,
                        tools=[],
                        llm_summary=f"Patch application failed in isolated validation: {apply_res.stderr}",
                        run_id=run_id,
                    )
                else:
                    validator_output = run_validation_in_copy(val_ws.temporary_root, config)
            progress.update(task, completed=100)
        except Exception as exc:
            progress.update(task, completed=100)
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="validator",
                error_type="validator_failed",
                message=str(exc),
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
            run_metadata.status = "failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            workspace_mgr.write_run_json(run_id, run_metadata)
            console.print(f"[bold red]Validator failed: {exc}[/bold red]")
            raise typer.Exit(code=1)

    # Write validation results
    workspace_mgr.write_artifact(run_id, "validation.json", validator_output.model_dump_json(indent=2))

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="validator",
        event="stage_end",
        data={"overall_passed": validator_output.overall_passed},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # 7. Update run metadata
    patch_checksum = hashlib.sha256(patch_diff.encode("utf-8")).hexdigest()
    validation_summary = "All checks passed successfully" if validator_output.overall_passed else (
        validator_output.llm_summary or "Validation failed"
    )
    model_metadata = {
        "executor": exec_meta,
        "validator": {
            "model_used": validator_output.model_used_for_summary,
            "overall_passed": validator_output.overall_passed,
        }
    }

    run_metadata.patch_checksum = patch_checksum
    run_metadata.validation_summary = validation_summary
    run_metadata.model_metadata = model_metadata
    run_metadata.status = "previewed"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    status_color = "green" if validator_output.overall_passed else "red"
    console.print(Panel(
        f"[bold green]✔ Preview and validation completed successfully![/bold green]\n"
        f"Run ID: [yellow]{run_id}[/yellow]\n"
        f"Validation Status: [bold {status_color}]{'PASSED' if validator_output.overall_passed else 'FAILED'}[/bold {status_color}]\n"
        f"Patch Checksum: [cyan]{patch_checksum[:12]}[/cyan]\n"
        f"Consolidated Patch: [cyan]{patch_path}[/cyan]\n"
        f"Validation Log: [cyan]{run_dir / 'validation.json'}[/cyan]",
        expand=False,
    ))


@app.command()
def apply(
    run_id: str = typer.Argument(..., help="Run ID of an existing run"),
    allow_dirty: bool = typer.Option(
        False,
        "--allow-dirty",
        help="Allow patch application even if the working tree has uncommitted changes",
    ),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
):
    """Apply the validated patch to the target repository."""
    console.print(
        Panel(
            f"[bold red]orchestrator Apply Patch (V1)[/bold red]\nRun ID: [yellow]{run_id}[/yellow]",
            expand=False,
        )
    )

    from datetime import datetime, timezone

    from orchestrator.agents.validator import run as run_validator
    from orchestrator.git import (
        apply_patch,
        check_patch,
        create_controlled_branch,
        current_head,
        repository_state,
        revert_apply,
    )
    from orchestrator.observability.events import log_event, log_failure
    from orchestrator.schemas.config import default_workspace_path

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
        raise typer.Exit(code=1)

    # 2. Read run.json and patch.diff
    run_metadata = workspace_mgr.read_run_json(run_id)
    target_path = Path(run_metadata.target_path)
    logs_dir = workspace_path / "logs"
    run_dir = workspace_mgr.run_dir(run_id)
    patch_path = run_dir / "patch.diff"

    if not patch_path.exists():
        console.print(f"[bold red]Error: patch.diff does not exist in {run_dir}[/bold red]")
        raise typer.Exit(code=1)

    # 3. Bootstrap target environment & load config
    bootstrap_environment(env_file=env_file, target_path=target_path)
    try:
        config = TargetConfig.load(target_path=target_path, workspace_path=workspace_path)
    except Exception as exc:
        console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
        raise typer.Exit(code=1)

    # 4. Perform Git Safety Checks
    # Verify valid git repo
    try:
        git_state = repository_state(target_path)
    except ValueError as exc:
        console.print(f"[bold red]Git Error: {exc}[/bold red]")
        raise typer.Exit(code=1)

    # Check cleanliness
    if not git_state.is_clean and not allow_dirty:
        console.print(
            "[bold red]Error: Target repository has uncommitted changes. "
            "Please commit, stash them, or run with --allow-dirty.[/bold red]"
        )
        raise typer.Exit(code=1)

    # Check commit compatibility
    curr_head = current_head(target_path)
    lifecycle_state = "VALID"
    if curr_head != run_metadata.base_commit:
        lifecycle_state = "REBASEABLE"
        # Check if patch applies cleanly
        chk_res = check_patch(target_path, patch_path)
        if chk_res.return_code != 0:
            lifecycle_state = "CONFLICT"
            console.print(
                f"[bold red]Error: Target repository is at HEAD {curr_head}, "
                f"which diverged from base commit {run_metadata.base_commit}. "
                f"The patch cannot be applied cleanly (Git Apply Check failed).[/bold red]"
            )
            run_metadata.lifecycle_state = "CONFLICT"
            run_metadata.updated_at = datetime.now(timezone.utc)
            workspace_mgr.write_run_json(run_id, run_metadata)
            raise typer.Exit(code=1)

    run_metadata.lifecycle_state = lifecycle_state
    workspace_mgr.write_run_json(run_id, run_metadata)

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="apply",
        event="stage_start",
        data={"lifecycle_state": lifecycle_state, "base_commit": run_metadata.base_commit, "current_head": curr_head},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    # 5. Check out explicit, system-controlled Git branch
    branch_name = f"patchforge/{run_id}"
    branch_res = create_controlled_branch(target_path, branch_name)
    if branch_res.return_code != 0:
        console.print(f"[bold red]Error checking out branch {branch_name}: {branch_res.stderr}[/bold red]")
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="apply",
            error_type="checkout_failed",
            message=branch_res.stderr,
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        raise typer.Exit(code=1)

    # 6. Apply patch
    apply_res = apply_patch(target_path, patch_path)
    if apply_res.return_code != 0:
        console.print(f"[bold red]Error applying patch: {apply_res.stderr}[/bold red]")
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="apply",
            error_type="apply_failed",
            message=apply_res.stderr,
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        # Revert: attempt to clean the partial apply
        revert_res = revert_apply(target_path)
        if revert_res.return_code != 0:
            console.print(
                "[bold red]FATAL: Patch application failed AND the automatic revert also failed. "
                f"Your repository may be in a partially applied state.\n"
                f"Revert stderr: {revert_res.stderr}\n"
                "Please run 'git checkout .' and 'git clean -fd' manually to restore a clean state.[/bold red]"
            )
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="apply",
                error_type="revert_failed",
                message=revert_res.stderr,
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
        # Write apply.json failure
        apply_data = {
            "run_id": run_id,
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "branch": branch_name,
            "success": False,
            "error": apply_res.stderr,
        }
        workspace_mgr.write_artifact(run_id, "apply.json", json.dumps(apply_data, indent=2))
        run_metadata.status = "failed"
        run_metadata.apply_status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        raise typer.Exit(code=1)

    # 7. Run post-apply validation checks
    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task("[green]Running post-apply validation checks...", total=None)
        try:
            post_val_output, _ = run_validator(config=config)
            progress.update(task, completed=100)
        except Exception as exc:
            progress.update(task, completed=100)
            console.print(f"[bold yellow]Warning: post-apply validation failed to execute: {exc}[/bold yellow]")
            post_val_output = None

    if post_val_output is not None:
        workspace_mgr.write_artifact(
            run_id, "post_apply_validation.json", post_val_output.model_dump_json(indent=2)
        )

    # 8. Write apply.json success
    apply_data = {
        "run_id": run_id,
        "applied_at": datetime.now(timezone.utc).isoformat(),
        "branch": branch_name,
        "success": True,
        "error": None,
    }
    workspace_mgr.write_artifact(run_id, "apply.json", json.dumps(apply_data, indent=2))

    # 9. Update metadata
    run_metadata.status = "applied"
    run_metadata.apply_status = "success"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    log_event(
        trace_id=run_id,
        run_id=run_id,
        level="info",
        source="pipeline",
        stage="apply",
        event="stage_end",
        data={"success": True, "post_apply_passed": post_val_output.overall_passed if post_val_output else None},
        logs_dir=logs_dir,
        run_dir=run_dir,
    )

    console.print(Panel(
        f"[bold green]✔ Patch applied successfully to branch [yellow]{branch_name}[/yellow]![/bold green]\n\n"
        f"To review and commit the changes, run:\n"
        f"  [cyan]git status[/cyan]\n"
        f"  [cyan]git diff[/cyan]\n"
        f"  [cyan]git commit -am \"Apply patch {run_id}\"[/cyan]",
        expand=False,
    ))


if __name__ == "__main__":
    app()
