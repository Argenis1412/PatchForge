import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.risk import RISK_GATE_JSON, RiskGateResult

if TYPE_CHECKING:
    from orchestrator.workspace import WorkspaceManager

# ── Infrastructure file heuristic ────────────────────────────────────────────

DANGEROUS_PATTERNS: set[str] = {
    "Dockerfile",
    "Makefile",
    "docker-compose.yml",
    ".github/workflows/",
    "Jenkinsfile",
    "requirements.txt",
    "setup.py",
    "setup.cfg",
    "pyproject.toml",
}


def _is_dangerous(path: str) -> bool:
    """Return True if *path* matches a known infrastructure file or directory.

    Matches either the basename (e.g. ``Dockerfile``) or a directory prefix
    (e.g. ``.github/workflows/deploy.yml`` matches ``.github/workflows/``).
    Also matches common variants like ``Dockerfile.prod``, ``Jenkinsfile.ci``,
    or ``docker-compose.prod.yml``.
    """
    p = Path(path)
    name = p.name
    if name in DANGEROUS_PATTERNS:
        return True
    if name.startswith("Dockerfile.") or name.startswith("Jenkinsfile."):
        return True
    if name.startswith("docker-compose.") and (name.endswith(".yml") or name.endswith(".yaml")):
        return True
    for parent in p.parents:
        candidate = str(parent).replace("\\", "/") + "/"
        if candidate in DANGEROUS_PATTERNS:
            return True
    return False


# ── Plan gate ─────────────────────────────────────────────────────────────────


def check_plan_gate(
    run_metadata: RunMetadata,
    architect_output: ArchitectOutput,
    workspace_mgr: Optional["WorkspaceManager"] = None,
) -> RiskGateResult:
    reasons: list[str] = []
    budget = run_metadata.risk_budget

    # Dangerous-file heuristic — escalate to high risk before budget checks
    for task in architect_output.implementation_plan:
        for f in task.files_to_modify:
            if _is_dangerous(f):
                task.risk_level = "high"
                reasons.append(f"File {f} is infrastructure — escalated to high risk")

    for task in architect_output.implementation_plan:
        if task.risk_level == "high":
            reasons.append(
                f"Task {task.task_id} ('{task.title}') is high-risk. "
                "High-risk tasks are not applicable in V1."
            )
        elif task.risk_level == "medium" and budget == "low":
            reasons.append(
                f"Task {task.task_id} ('{task.title}') is medium-risk "
                f"but risk_budget is '{budget}'."
            )

    files = set()
    for task in architect_output.implementation_plan:
        files.update(task.files_to_modify)
    if len(files) > run_metadata.max_files:
        reasons.append(
            f"Plan modifies {len(files)} file(s), exceeding max_files={run_metadata.max_files}."
        )

    risk_result = RiskGateResult(
        passed=len(reasons) == 0,
        gate="plan",
        reasons=reasons,
    )

    if workspace_mgr is not None:
        workspace_mgr.write_artifact(
            run_metadata.run_id,
            RISK_GATE_JSON,
            risk_result.model_dump_json(indent=2),
        )
        if not risk_result.passed:
            if run_metadata.failure_artifacts is None:
                run_metadata.failure_artifacts = []
            if RISK_GATE_JSON not in run_metadata.failure_artifacts:
                run_metadata.failure_artifacts.append(RISK_GATE_JSON)

    return risk_result


# ── Diff-counting helpers ────────────────────────────────────────────────────


def _count_diff_lines(diff_text: str) -> int:
    count = 0
    for line in diff_text.splitlines():
        if not line:
            continue
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("diff --git"):
            continue
        if line.startswith("index "):
            continue
        if line.startswith("new file"):
            continue
        if line.startswith("deleted file"):
            continue
        if line.startswith("+") or line.startswith("-"):
            count += 1
    return count


def _count_diff_files(diff_text: str) -> int:
    files = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            parts = shlex.split(line)
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    files.add(b_path[2:])
    return len(files)


# ── Patch gate ────────────────────────────────────────────────────────────────


def check_patch_gate(
    run_metadata: RunMetadata,
    patch_diff: str,
    workspace_mgr: Optional["WorkspaceManager"] = None,
) -> RiskGateResult:
    reasons: list[str] = []

    file_count = _count_diff_files(patch_diff)
    if file_count > run_metadata.max_files:
        reasons.append(
            f"Patch modifies {file_count} file(s), exceeding max_files={run_metadata.max_files}."
        )

    line_count = _count_diff_lines(patch_diff)
    if line_count > run_metadata.max_diff_lines:
        reasons.append(
            f"Patch has {line_count} diff line(s), "
            f"exceeding max_diff_lines={run_metadata.max_diff_lines}."
        )

    risk_result = RiskGateResult(
        passed=len(reasons) == 0,
        gate="patch",
        reasons=reasons,
    )

    if workspace_mgr is not None:
        workspace_mgr.write_artifact(
            run_metadata.run_id,
            RISK_GATE_JSON,
            risk_result.model_dump_json(indent=2),
        )
        if not risk_result.passed:
            if run_metadata.failure_artifacts is None:
                run_metadata.failure_artifacts = []
            if RISK_GATE_JSON not in run_metadata.failure_artifacts:
                run_metadata.failure_artifacts.append(RISK_GATE_JSON)

    return risk_result
