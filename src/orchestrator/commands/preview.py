"""Preview command: generates patch via Executor and validates via Validator."""

__all__ = [
    "execute",
]

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from orchestrator.agents import executor as executor_agent
from orchestrator.agents.validator.runners import DEFAULT_TIMEOUT
from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.observability.events import log_event, log_failure
from orchestrator.risk import check_patch_gate
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig, default_workspace_path
from orchestrator.schemas.executor_output import TaskStatus
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
    force_provider: Optional[str] = None,
    validator_timeout: Optional[int] = None,
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
        raise typer.Exit(code=1) from None

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
        raise typer.Exit(code=1) from None

    # 2.5 Verify experiment context if experiment.json is present
    from orchestrator.schemas.experiment import verify_experiment_or_warn

    try:
        verify_experiment_or_warn(workspace_mgr, run_id, target_path)
    except ValueError as exc:
        console.print(f"[bold red]Validation Error: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

    # 3. Bootstrap target environment & load config
    bootstrap_environment(env_file=env_file, target_path=target_path)
    try:
        config = TargetConfig.load(
            target_path=target_path,
            workspace_path=workspace_path,
            validator_timeout=validator_timeout,
        )
    except Exception as exc:
        console.print(f"[bold red]Error loading target config: {exc}[/bold red]")
        raise typer.Exit(code=1) from None

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

    # Always clean staging to ensure a fresh execution cycle.
    cleaned_count = 0
    if staging_dir.exists():
        try:
            cleaned_count = sum(1 for _ in staging_dir.rglob("*") if _.is_file())
            shutil.rmtree(staging_dir)
        except OSError as exc:
            log_failure(
                trace_id=run_id,
                run_id=run_id,
                stage="executor",
                error_type="staging_cleanup_failed",
                message=str(exc),
                logs_dir=logs_dir,
                run_dir=run_dir,
            )
            run_metadata.status = "failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            workspace_mgr.write_run_json(run_id, run_metadata)
            console.print(f"[bold red]Error clearing staging directory: {exc}[/bold red]")
            raise typer.Exit(code=1) from None
    staging_dir.mkdir(parents=True, exist_ok=True)

    if cleaned_count > 0:
        log_event(
            trace_id=run_id,
            run_id=run_id,
            level="info",
            source="pipeline",
            stage="executor",
            event="staging_cleaned",
            data={"files_removed": cleaned_count},
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        console.print(
            f"[yellow]Staging limpiado: se limpiaron {cleaned_count} archivos previos[/yellow]"
        )

    if force_provider is not None:
        log_event(
            trace_id=run_id,
            run_id=run_id,
            level="info",
            source="pipeline",
            stage="executor",
            event="force_provider_override",
            data={"provider": force_provider, "source": "cli"},
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        console.print(f"[yellow]Override activo: todos los tasks usarán {force_provider}[/yellow]")

    with Progress(
        SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console
    ) as progress:
        task = progress.add_task(
            "[green]Executing planned tasks and generating patch...", total=None
        )
        try:
            executor_output, exec_meta = executor_agent.run(
                architect_output=architect_output,
                run_id=run_id,
                config=config,
                staging_dir=staging_dir,
                force_provider=force_provider,
                logs_dir=logs_dir,
                run_dir=run_dir,
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
            raise typer.Exit(code=1) from None

    # 4.5. Show error panel if any tasks failed
    if executor_output.errors:
        error_lines = []
        for err_change in executor_output.errors:
            error_lines.append(
                f"[red]• {err_change.task_id}[/red] ({err_change.file}): {err_change.error}"
            )
        console.print(
            Panel(
                "\n".join(error_lines),
                title="[bold red]Executor Errors[/bold red]",
                expand=False,
            )
        )

    hard_errors = [e for e in executor_output.errors if e.status == TaskStatus.ERROR]

    # 5. Consolidate file changes into a single patch.diff
    diffs = []
    for change in executor_output.applied + executor_output.pending_review:
        if change.diff:
            diffs.append(change.diff)
    patch_diff = "\n".join(diffs)

    if not patch_diff:
        for stale_artifact in ("patch.diff", "validation.json"):
            stale_path = run_dir / stale_artifact
            if stale_path.exists():
                stale_path.unlink()
        log_failure(
            trace_id=run_id,
            run_id=run_id,
            stage="executor",
            error_type="empty_patch",
            message=(
                f"No diffs generated. Applied: {len(executor_output.applied)}, "
                f"pending_review: {len(executor_output.pending_review)}, "
                f"errors: {len(executor_output.errors)}. "
                "All tasks may have resulted in NOOP."
            ),
            logs_dir=logs_dir,
            run_dir=run_dir,
        )
        run_metadata.status = "failed"
        run_metadata.updated_at = datetime.now(timezone.utc)
        workspace_mgr.write_run_json(run_id, run_metadata)
        console.print(
            "[bold red]No changes were generated by the executor (empty patch).[/bold red]\n"
            "[yellow]Possible causes: all tasks NOOP, stale staging, "
            "LLM idempotent response.[/yellow]"
        )
        raise typer.Exit(code=1) from None

    # 6. Check patch gate on the consolidated diff
    risk_result = check_patch_gate(run_metadata, patch_diff, workspace_mgr=workspace_mgr)
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
        raise typer.Exit(code=1) from None

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
        task = progress.add_task("[green]Preparing validation workspace...", total=None)

        def _update_progress(msg: str) -> None:
            progress.update(task, description=f"[green]{msg}")

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
                    validator_output = run_validation_in_copy(
                        val_ws.temporary_root, config, progress_callback=_update_progress
                    )
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
            raise typer.Exit(code=1) from None

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

    timeout_tools = [t for t in validator_output.tools if t.timed_out]

    prefix_parts: list[str] = []
    if hard_errors:
        failed_ids = ", ".join(sorted(e.task_id for e in hard_errors))
        prefix_parts.append(
            f"Incomplete deliverables: {len(hard_errors)} task(s) failed ({failed_ids}). "
        )
    if timeout_tools:
        tool_names = ", ".join(t.tool for t in timeout_tools)
        effective_timeout = config.validator_timeout or DEFAULT_TIMEOUT
        prefix_parts.append(
            f"Timeout: {tool_names} exceeded {effective_timeout}s limit. "
            f"Increase with --validator-timeout <seconds>. "
        )

    base = (
        "All checks passed successfully"
        if validator_output.overall_passed and not hard_errors
        else (validator_output.llm_summary or "Validation failed")
    )
    validation_summary = "".join(prefix_parts) + base

    model_metadata = {
        "executor": exec_meta,
        "validator": {
            "model_used": validator_output.model_used_for_summary,
            "overall_passed": validator_output.overall_passed,
        },
    }

    run_metadata.executor_had_errors = bool(hard_errors)
    if hard_errors:
        run_metadata.status = "validation_failed"
    elif validator_output.overall_passed:
        run_metadata.status = "previewed"
    else:
        run_metadata.status = "validation_failed"

    run_metadata.patch_checksum = patch_checksum
    run_metadata.validation_summary = validation_summary
    run_metadata.model_metadata = model_metadata
    run_metadata.updated_at = datetime.now(timezone.utc)
    workspace_mgr.write_run_json(run_id, run_metadata)

    success = validator_output.overall_passed and not hard_errors
    status_color = "green" if success else "red"
    validation_label = "PASSED" if success else "FAILED"
    timeout_hint = ""
    if timeout_tools:
        tool_names = ", ".join(t.tool for t in timeout_tools)
        effective_timeout = config.validator_timeout or DEFAULT_TIMEOUT
        timeout_hint = (
            f"\n[yellow]Timeout:[/yellow] {tool_names} exceeded {effective_timeout}s limit."
            f"\n         Increase with --validator-timeout <seconds>"
        )
    title_line = (
        "[bold green]✔ Preview and validation completed successfully![/bold green]"
        if success
        else "[bold red]✘ Preview completed with failures[/bold red]"
    )
    console.print(
        Panel(
            f"{title_line}\n"
            f"Run ID: [yellow]{run_id}[/yellow]\n"
            f"Validation Status: [bold {status_color}]{validation_label}[/bold {status_color}]\n"
            f"Patch Checksum: [cyan]{patch_checksum[:12]}[/cyan]\n"
            f"Consolidated Patch: [cyan]{patch_path}[/cyan]\n"
            f"Validation Log: [cyan]{run_dir / 'validation.json'}[/cyan]"
            f"{timeout_hint}",
            expand=False,
        )
    )
