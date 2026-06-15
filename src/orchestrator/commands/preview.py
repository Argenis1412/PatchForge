import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from orchestrator.agents import executor as executor_agent
from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.observability.events import log_event, log_failure
from orchestrator.risk import check_patch_gate
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig, default_workspace_path
from orchestrator.schemas.validator_output import ValidatorOutput
from orchestrator.validation_workspace import (
    apply_patch_to_copy,
    create_validation_workspace,
    run_validation_in_copy,
)
from orchestrator.workspace import WorkspaceManager

console = Console()


def execute(
    run_id: str,
    workspace: Optional[Path] = None,
    env_file: Optional[Path] = None,
) -> None:
    console.print(
        Panel(
            f"[bold yellow]PatchForge Preview & Validation (V1)[/bold yellow]"
            f"\nRun ID: [yellow]{run_id}[/yellow]",
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

    # 2.5 Verify experiment context if experiment.json is present
    from orchestrator.schemas.experiment import verify_experiment_or_warn

    try:
        verify_experiment_or_warn(workspace_mgr, run_id, target_path)
    except ValueError as exc:
        console.print(f"[bold red]Validation Error: {exc}[/bold red]")
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
        task = progress.add_task(
            "[green]Executing planned tasks and generating patch...", total=None
        )
        try:
            executor_output, exec_meta = executor_agent.run(
                architect_output=architect_output, config=config, staging_dir=staging_dir
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

    # 6. Check patch gate on the consolidated diff
    risk_result = check_patch_gate(run_metadata, patch_diff)
    if not risk_result.passed:
        console.print("[bold red]Patch blocked by size gate:[/bold red]")
        for reason in risk_result.reasons:
            console.print(f"  - {reason}")
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="executor",
            error_type="risk_gate_blocked",
            message="; ".join(risk_result.reasons),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        raise typer.Exit(code=1)

    # 7. Write patch.diff
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

    # 8. Run Validator in temporary copy
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
            with create_validation_workspace(
                original_root=target_path, patch_path=patch_path
            ) as val_ws:
                apply_res = apply_patch_to_copy(val_ws.temporary_root, val_ws.patch_path)
                if apply_res.return_code != 0:
                    validator_output = ValidatorOutput(
                        overall_passed=False,
                        tools=[],
                        llm_summary=(
                            f"Patch application failed in isolated validation: {apply_res.stderr}"
                        ),
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
    workspace_mgr.write_artifact(
        run_id, "validation.json", validator_output.model_dump_json(indent=2)
    )

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

    # 9. Update run metadata
    patch_checksum = hashlib.sha256(patch_diff.encode("utf-8")).hexdigest()
    validation_summary = (
        "All checks passed successfully"
        if validator_output.overall_passed
        else (validator_output.llm_summary or "Validation failed")
    )
    model_metadata = {
        "executor": exec_meta,
        "validator": {
            "model_used": validator_output.model_used_for_summary,
            "overall_passed": validator_output.overall_passed,
        },
    }

    run_metadata.patch_checksum = patch_checksum
    run_metadata.validation_summary = validation_summary
    run_metadata.model_metadata = model_metadata
    run_metadata.status = "previewed" if validator_output.overall_passed else "validation_failed"
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    status_color = "green" if validator_output.overall_passed else "red"
    validation_label = "PASSED" if validator_output.overall_passed else "FAILED"
    console.print(
        Panel(
            f"[bold green]✔ Preview and validation completed successfully![/bold green]\n"
            f"Run ID: [yellow]{run_id}[/yellow]\n"
            f"Validation Status: [bold {status_color}]{validation_label}[/bold {status_color}]\n"
            f"Patch Checksum: [cyan]{patch_checksum[:12]}[/cyan]\n"
            f"Consolidated Patch: [cyan]{patch_path}[/cyan]\n"
            f"Validation Log: [cyan]{run_dir / 'validation.json'}[/cyan]",
            expand=False,
        )
    )
