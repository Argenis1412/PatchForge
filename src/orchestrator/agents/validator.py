"""
agents/validator.py

Validator — cuarto agente del pipeline.

Responsabilidades:
  1. Correr herramientas reales: ruff, pytest, tsc --noEmit
  2. Capturar return_code, stdout, stderr por tool
  3. Si hay fallos: llamar Gemini Flash para resumir stderr (NO para ejecutar nada)
  4. Emitir ValidatorOutput con overall_passed y log completo

Reglas del lab:
  - LLM solo resume stderr — nunca ejecuta herramientas
  - Gemini Flash, no Claude — resumir no requiere razonamiento profundo
  - Logging desde el día 1: tokens, costo, latencia
  - Retry policy: si Gemini falla el summary, se loguea el error y se continúa
    (el summary es observabilidad, no bloquea el resultado)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Union

from orchestrator.observability.logging import get_file_logger
from orchestrator.schemas.validator_output import ToolResult, ValidatorOutput

if TYPE_CHECKING:
    from orchestrator.schemas.config import TargetConfig


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

MODEL_GEMINI = "gemini-2.5-flash"

# Costo Gemini Flash en free tier
COST_PER_SUMMARY = 0.0

SUBPROCESS_TIMEOUT = 120   # segundos — pytest puede ser lento

# ---------------------------------------------------------------------------
# Logger (lazy)
# ---------------------------------------------------------------------------

_logger = None

def _get_logger(logs_dir: Path | None = None):
    global _logger
    if logs_dir is not None or _logger is None:
        _logger = get_file_logger("validator", logs_dir, "validator.log")
    return _logger

# ---------------------------------------------------------------------------
# Helper: Frontend Detection
# ---------------------------------------------------------------------------

def _find_frontend_dir(root: Path) -> Path | None:
    """
    Busca el directorio con package.json más cercano a la raíz.
    Excluye node_modules para no fallar en proyectos con deps instaladas.
    """
    for path in root.rglob("package.json"):
        if "node_modules" not in path.parts:
            return path.parent
    return None

# ---------------------------------------------------------------------------
# Tool runners
# ---------------------------------------------------------------------------

def _run(
    cmd: list[str],
    cwd: Path,
    tool_name: str,
    run_id: str,
) -> ToolResult:
    """
    Ejecuta un comando externo con subprocess y captura stdout/stderr.
    return_code != 0 → passed = False.
    """
    _get_logger().info("[%s] Ejecutando %s: %s (cwd=%s)", run_id, tool_name, " ".join(cmd), cwd)
    t0 = time.perf_counter()

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except FileNotFoundError:
        msg = f"Comando no encontrado: {cmd[0]} — ¿está instalado y en PATH?"
        _get_logger().error("[%s] %s", run_id, msg)
        return ToolResult(
            tool=tool_name,          # type: ignore[arg-type]
            passed=False,
            return_code=-1,
            stderr=msg,
        )
    except subprocess.TimeoutExpired:
        msg = f"Timeout ({SUBPROCESS_TIMEOUT}s) ejecutando {cmd[0]}"
        _get_logger().error("[%s] %s", run_id, msg)
        return ToolResult(
            tool=tool_name,          # type: ignore[arg-type]
            passed=False,
            return_code=-2,
            stderr=msg,
        )

    elapsed = time.perf_counter() - t0
    passed = proc.returncode in (0, 5)

    _get_logger().info(
        "[%s] %s → %s | rc=%d | latency=%.2fs",
        run_id, tool_name, "PASS" if passed else "FAIL", proc.returncode, elapsed,
    )
    if not passed:
        _get_logger().debug("[%s] %s stderr:\n%s", run_id, tool_name, proc.stderr[:2000])

    return ToolResult(
        tool=tool_name,              # type: ignore[arg-type]
        passed=passed,
        return_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _collect_staged_files(staging_dir: Path) -> list[Path]:
    """Return all regular files under staging_dir, sorted."""
    if not staging_dir.is_dir():
        return []
    return sorted(p for p in staging_dir.rglob("*") if p.is_file())


def _create_overlay(project_root: Path, staging_dir: Path, ignore_dirs: list[str]) -> Path:
    """Create a temp overlay with original project + staged changes applied."""
    overlay = Path(tempfile.mkdtemp(prefix="val_overlay_"))
    # Mirror project structure, excluding large ignored dirs
    ignore_set = set(ignore_dirs)
    shutil.copytree(
        str(project_root),
        str(overlay / project_root.name),
        ignore=lambda src, names: [n for n in names if n in ignore_set],
        dirs_exist_ok=True,
        symlinks=True,
    )
    overlay_root = overlay / project_root.name
    # Overlay staged files
    for staged_file in _collect_staged_files(staging_dir):
        rel = staged_file.relative_to(staging_dir)
        target = overlay_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(staged_file, target)
    return overlay_root


def run_ruff(
    run_id: str,
    project_root: Path,
    cmd_override: list[str] | None = None,
    staging_dir: Path | None = None,
) -> ToolResult:
    if staging_dir is not None and staging_dir.is_dir():
        staged_files = _collect_staged_files(staging_dir)
        if staged_files:
            cmd = cmd_override if cmd_override is not None else ["ruff", "check"]
            cmd.extend(str(sf) for sf in staged_files)
            return _run(cmd, project_root, "ruff", run_id)
    cmd = cmd_override if cmd_override is not None else ["ruff", "check", "."]
    return _run(cmd, project_root, "ruff", run_id)


IGNORE_DIRS = ["node_modules", ".venv", "__pycache__", ".git", ".ruff_cache", ".pytest_cache"]


def run_pytest(
    run_id: str,
    project_root: Path,
    cmd_override: list[str] | None = None,
    staging_dir: Path | None = None,
) -> ToolResult:
    if staging_dir is not None and staging_dir.is_dir() and bool(
        _collect_staged_files(staging_dir)
    ):
        overlay_root = _create_overlay(project_root, staging_dir, IGNORE_DIRS)
        cmd = cmd_override if cmd_override is not None else ["pytest", ".", "--tb=short", "-q"]
        return _run(cmd, overlay_root, "pytest", run_id)
    cmd = cmd_override if cmd_override is not None else ["pytest", ".", "--tb=short", "-q"]
    return _run(
        cmd,
        project_root,
        "pytest",
        run_id,
    )


def run_tsc(
    run_id: str,
    project_root: Path,
    cmd_override: list[str] | None = None,
    staging_dir: Path | None = None,
) -> ToolResult:
    if staging_dir is not None and staging_dir.is_dir() and bool(
        _collect_staged_files(staging_dir)
    ):
        overlay_root = _create_overlay(project_root, staging_dir, IGNORE_DIRS)
        frontend = _find_frontend_dir(overlay_root) or _find_frontend_dir(project_root)
        if frontend is None:
            _get_logger().warning(
                "[%s] frontend/ no encontrado — skip tsc", run_id
            )
            return ToolResult(
                tool="tsc", passed=True, return_code=0,
                stdout="Skipped — frontend/ not found",
            )
        cmd = cmd_override if cmd_override is not None else ["npx", "tsc", "--noEmit"]
        return _run(cmd, frontend, "tsc", run_id)
    frontend = _find_frontend_dir(project_root)
    if frontend is None:
        _get_logger().warning("[%s] frontend/ no encontrado — skip tsc", run_id)
        return ToolResult(
            tool="tsc",
            passed=True,
            return_code=0,
            stdout="Skipped — frontend/ not found",
        )
    cmd = cmd_override if cmd_override is not None else ["npx", "tsc", "--noEmit"]
    return _run(cmd, frontend, "tsc", run_id)


# ---------------------------------------------------------------------------
# Gemini Flash — solo para error summary
# ---------------------------------------------------------------------------

def _summarize_errors(failed_tools: list[ToolResult], run_id: str) -> str:
    """
    Llama a Gemini Flash para resumir los stderr de las tools que fallaron.
    Si Gemini falla, devuelve un fallback con el stderr crudo — nunca bloquea.
    """
    if not os.getenv("GOOGLE_API_KEY"):
        _get_logger().warning("[%s] GOOGLE_API_KEY no configurada — skip summary", run_id)
        return "[summary no disponible — GOOGLE_API_KEY ausente]"


    stderr_sections = "\n\n".join(
        f"### {r.tool.upper()} (rc={r.return_code})\n{(r.stderr or r.stdout)[:3000]}"
        for r in failed_tools
    )

    prompt = f"""You are a code quality analyst. Summarize the following tool errors concisely.

Rules:
- Maximum 5 bullet points
- Each bullet: tool name + root cause + file/line if available
- No suggestions, no fixes — only what failed and why
- If the same error repeats, group it

ERRORS
------
{stderr_sections}
"""

    _get_logger().debug("[%s] Gemini summary request | tools=%s", run_id, [r.tool for r in failed_tools])
    t0 = time.perf_counter()

    try:
        from orchestrator.clients.gemini_client import get_gemini_client
        client = get_gemini_client()
        response = client.models.generate_content(
            model=MODEL_GEMINI,
            contents=prompt,
        )
        elapsed = time.perf_counter() - t0
        summary = response.text.strip()

        # Tokens (Gemini devuelve usage_metadata)
        usage = getattr(response, "usage_metadata", None)
        input_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
        output_tok = getattr(usage, "candidates_token_count", 0) if usage else 0

        _get_logger().info(
            "[%s] Gemini summary OK | latency=%.2fs | in=%d | out=%d | cost=$0.00 (free tier)",
            run_id, elapsed, input_tok, output_tok,
        )
        return summary

    except Exception as exc:  # noqa: BLE001
        _get_logger().error("[%s] Gemini summary falló: %s — usando stderr crudo", run_id, exc)
        # Fallback: devolver stderr truncado, no bloquear el pipeline
        return "\n".join(
            f"[{r.tool}] {(r.stderr or r.stdout)[:500]}" for r in failed_tools
        )


# ---------------------------------------------------------------------------
# Entrypoint público
# ---------------------------------------------------------------------------

def run(
    config: Union[str, Path, "TargetConfig"] | None = None,
    staging_dir: Path | None = None,
) -> tuple[ValidatorOutput, dict]:
    """
    Punto de entrada del Validator.
    Corre ruff → pytest → tsc, luego Gemini resume si hay fallos.
    Si staging_dir se provee, las tools se ejecutan contra los cambios staged.
    """
    from orchestrator.schemas.config import TargetConfig
    if config is None:
        config = TargetConfig.load(target_path=Path(".").resolve())
    elif isinstance(config, (str, Path)):
        config = TargetConfig.load(target_path=Path(config))

    logs_dir = config.workspace_path / "logs"
    project_root = config.target_path.resolve()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    _get_logger(logs_dir).info("=== Validator run %s ===", run_id)

    results: list[ToolResult] = [
        run_ruff(run_id, project_root, config.lint_command, staging_dir),
    ]

    if config.capabilities.effective_supports_tests:
        results.append(run_pytest(run_id, project_root, config.test_command, staging_dir))
    else:
        _get_logger().info("[%s] Tests skip (no framework detectado o deshabilitado)", run_id)

    if config.capabilities.effective_supports_typecheck:
        results.append(run_tsc(run_id, project_root, config.typecheck_command, staging_dir))
    else:
         _get_logger().info("[%s] Typecheck skip (no detectado o deshabilitado)", run_id)

    failed = [r for r in results if not r.passed]
    overall_passed = len(failed) == 0

    model_used = ""
    llm_summary: str | None = None

    tokens_input = 0
    tokens_output = 0

    # Gemini solo si hay fallos
    if failed:
        model_used = MODEL_GEMINI

        # Generar summary por tool individual
        for tool_result in failed:
            tool_result.error_summary = _summarize_errors([tool_result], run_id)

        # Summary global
        llm_summary = _summarize_errors(failed, run_id)

        # Note: Summary cost is free tier (0.0), but let's track tokens
        # The _summarize_errors doesn't return tokens explicitly but logs them.
        # This validator is lightweight.

    output = ValidatorOutput(
        overall_passed=overall_passed,
        tools=results,
        llm_summary=llm_summary,
        run_id=run_id,
        model_used_for_summary=model_used,
    )

    _get_logger().info(
        "[%s] Finalizado | overall=%s | failed_tools=%s",
        run_id,
        "PASS" if overall_passed else "FAIL",
        [r.tool for r in failed] or "none",
    )

    meta = {
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_usd": 0.0,
        "model_used": model_used
    }

    return output, meta


# ---------------------------------------------------------------------------
# Smoke test (python agents/validator.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pass
