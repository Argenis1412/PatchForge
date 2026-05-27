import sys
import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.panel import Panel

# Force UTF-8 encoding for Rich progress bars on Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
from rich.progress import Progress, SpinnerColumn, TextColumn

from agent_lab.schemas.config import TargetConfig
from agent_lab.clients.bootstrap import bootstrap_environment
from agent_lab.pipeline import Pipeline
from agent_lab.agents.scout import run as run_scout

app = typer.Typer(help="Agent Lab CLI - Automated code execution and validation.")
console = Console()

@app.command()
def run(
    path: Path = typer.Argument(..., help="Target project path"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Run the pipeline until the Executor stage, but don't apply changes"),
    from_stage: Optional[str] = typer.Option(None, "--from-stage", help="Stage to resume from (scout, architect, executor)"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Path to the workspace directory"),
):
    """Run the full agent-lab pipeline on a target project."""
    console.print(Panel(f"[bold cyan]Agent Lab Pipeline[/bold cyan]\nTarget: [yellow]{path.absolute()}[/yellow]", expand=False))
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        progress.add_task("[cyan]Bootstrapping environment...", total=None)
        bootstrap_environment(env_file=env_file, target_path=path)
        config = TargetConfig.load(target_path=path, workspace_path=workspace)
        
    pipeline = Pipeline(config=config, from_stage=from_stage)
    
    console.print("[bold blue]Starting pipeline execution...[/bold blue]")
    result = pipeline.execute(dry_run=dry_run)
    
    status = result.status
    if status == "success":
        console.print(Panel(f"[bold green]Pipeline finished successfully![/bold green]\nRun ID: {result.run_id}", expand=False))
    else:
        console.print(Panel(f"[bold red]Pipeline failed.[/bold red]\nRun ID: {result.run_id}", expand=False))
        raise typer.Exit(code=1)


@app.command()
def scan(
    path: Path = typer.Argument(..., help="Target project path"),
    env_file: Optional[Path] = typer.Option(None, "--env-file", help="Path to a custom .env file"),
    workspace: Optional[Path] = typer.Option(None, "--workspace", help="Path to the workspace directory"),
):
    """Run only the Scout agent (reconnaissance) on a target project."""
    console.print(Panel(f"[bold magenta]Agent Lab Scout[/bold magenta]\nTarget: [yellow]{path.absolute()}[/yellow]", expand=False))
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        progress.add_task("[cyan]Bootstrapping environment...", total=None)
        bootstrap_environment(env_file=env_file, target_path=path)
        config = TargetConfig.load(target_path=path, workspace_path=workspace)
    
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task(f"[green]Scanning {config.target_path}...", total=None)
        output, meta = run_scout(config)
        progress.update(task, completed=100)
    
    console.print(f"[bold green]✔ Scout completed in[/bold green] {meta.get('latency_ms', 0)}ms")
    console.print(f"Discovered [bold cyan]{len(output.hotspots)}[/bold cyan] findings.")

if __name__ == "__main__":
    app()
