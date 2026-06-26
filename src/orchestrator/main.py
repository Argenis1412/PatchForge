"""PatchForge CLI entrypoint."""

__all__ = [
    "app",
]

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

# Force UTF-8 encoding for Rich progress bars on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
from orchestrator.clients.bootstrap import bootstrap_environment
from orchestrator.schemas.config import TargetConfig

app = typer.Typer(
    name="patchforge", help="PatchForge - deterministic patch planning and execution."
)
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
) -> None:
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


@app.command(hidden=True)
def run(
    path: Optional[Path] = typer.Argument(
        default=None,
        help="Ignored. Deprecated command.",
    ),
) -> None:
    """Deprecated. Use doctor, scan, plan, preview, and apply instead."""
    console.print(
        "[yellow]Warning: `patchforge run` is deprecated and hidden in V1.\n\n"
        "Use the new V1 workflow:\n"
        "  patchforge doctor .\n"
        "  patchforge scan .\n"
        "  patchforge plan <run_id>\n"
        "  patchforge preview <run_id>\n"
        "  patchforge apply <run_id>[/yellow]"
    )


@app.command()
def scan(
    path: Path = typer.Argument(..., help="Target project path"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
    risk_budget: Optional[str] = typer.Option(
        None,
        "--risk-budget",
        help="Risk budget: 'low', 'medium', or 'high'",
    ),
) -> None:
    """Scan a target project using deterministic analysis (no AI)."""
    from orchestrator.commands.scan import execute as execute_scan

    if risk_budget is not None and risk_budget not in ("low", "medium", "high"):
        console.print(
            "[bold red]Error: Invalid value for --risk-budget. "
            "Valid options are 'low', 'medium', or 'high'.[/bold red]"
        )
        raise typer.Exit(1)

    config = _load_target_config(path=path, workspace=workspace, env_file=env_file)
    execute_scan(config=config, risk_budget=risk_budget)


@app.command()
def plan(
    run_id: str = typer.Argument(..., help="Run ID of an existing run"),
    issue_file: Optional[Path] = typer.Option(
        None, "--issue-file", help="Path to a markdown issue file with frontmatter"
    ),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
) -> None:
    """Run the Architect agent to generate an implementation plan for a run."""
    from orchestrator.commands.plan import execute as execute_plan

    execute_plan(run_id=run_id, workspace=workspace, env_file=env_file, issue_file=issue_file)


@app.command()
def preview(
    run_id: str = typer.Argument(..., help="Run ID of an existing run"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace", help="Path to the workspace directory"
    ),
    force_provider: Optional[str] = typer.Option(
        None,
        "--force-provider",
        help="Force a specific LLM ('gemini'|'groq'|'claude') for all tasks, "
        "ignoring risk_level routing. Does not affect high-risk gating.",
    ),
) -> None:
    """Generate and validate a unified patch without modifying the target repository."""
    from orchestrator.agents.executor.providers import KNOWN_PROVIDER_NAMES
    from orchestrator.commands.preview import execute as execute_preview

    if force_provider is not None and force_provider not in KNOWN_PROVIDER_NAMES:
        console.print(
            f"[bold red]Error: Invalid value for --force-provider. "
            f"Valid options are: {', '.join(KNOWN_PROVIDER_NAMES)}.[/bold red]"
        )
        raise typer.Exit(1)

    execute_preview(
        run_id=run_id, workspace=workspace, env_file=env_file, force_provider=force_provider
    )


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
) -> None:
    """Apply the validated patch to the target repository."""
    from orchestrator.commands.apply import execute as execute_apply

    execute_apply(
        run_id=run_id,
        allow_dirty=allow_dirty,
        env_file=env_file,
        workspace=workspace,
    )


if __name__ == "__main__":
    app()
