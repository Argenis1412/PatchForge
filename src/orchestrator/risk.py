import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from orchestrator.schemas.architect_output import ArchitectOutput, Task
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.risk import RISK_GATE_JSON, RiskGateResult

if TYPE_CHECKING:
    from orchestrator.workspace import WorkspaceManager

# ── Source-code extension heuristic ──────────────────────────────────────────

_SOURCE_EXTENSIONS: frozenset[str] = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})


def _is_code_gen(task: Task) -> bool:
    return any(Path(f).suffix in _SOURCE_EXTENSIONS for f in task.files_to_modify)


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


# ── File-semantic taxonomy (P4 — qualitative risk gates) ────────────────────

_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

FILE_TAXONOMY: dict[str, str] = {
    "schemas/": "high",
    "migrations/": "high",
    "alembic/": "high",
    "src/orchestrator/schemas/": "high",
    "config/": "medium",
    "scripts/": "medium",
    "tests/": "low",
    "docs/": "low",
}


def _taxonomy_risk(path: str) -> str | None:
    """Return the highest taxonomy risk tier for *path*, or None if no rule matches.

    Uses normalized forward-slash prefix matching, consistent with
    ``_is_dangerous()`` path handling. All matching prefixes are considered —
    the result is the highest tier among them, independent of dict insertion
    order (e.g. ``tests/config/settings.py`` matches both ``tests/`` and
    ``config/``; the higher tier, ``medium``, wins).
    """
    normalized = "/" + path.replace("\\", "/")
    matched_tiers = [tier for prefix, tier in FILE_TAXONOMY.items() if f"/{prefix}" in normalized]
    if not matched_tiers:
        return None
    return max(matched_tiers, key=lambda t: _RISK_ORDER[t])


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

    # File-semantic taxonomy — escalate to the highest taxonomy tier among the
    # task's files, if that tier outranks the current risk_level. All files
    # that matched the escalating tier are recorded (not just the first),
    # consistent with the DANGEROUS_PATTERNS loop above.
    for task in architect_output.implementation_plan:
        matches = [(f, _taxonomy_risk(f)) for f in task.files_to_modify]
        matches = [(f, tier) for f, tier in matches if tier is not None]
        if not matches:
            continue
        max_tier = max((tier for _, tier in matches), key=lambda t: _RISK_ORDER[t])
        if _RISK_ORDER[max_tier] > _RISK_ORDER.get(task.risk_level, 0):
            old = task.risk_level
            task.risk_level = max_tier
            for f, tier in matches:
                if tier == max_tier:
                    reasons.append(f"taxonomy: {f} → {max_tier} (was {old})")

    # Code-gen floor — low-risk code tasks escalate to medium
    for task in architect_output.implementation_plan:
        if task.risk_level == "low" and _is_code_gen(task):
            task.risk_level = "medium"

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


def parse_diff_files(diff_text: str) -> set[str]:
    """Extract file paths touched by a unified diff.

    Emits only the b-side path from each ``diff --git a/X b/Y`` header.
    Skips ``/dev/null`` (deletes — the file no longer exists on disk).
    For renames the b-side is the destination; the a-side no longer exists
    after ``git apply``, so emitting it would cause ``pathspec did not match``.
    Binary and mode-only changes are included via header presence.
    """
    files: set[str] = set()
    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            parts = shlex.split(line)
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/") and b_path != "b//dev/null":
                    files.add(b_path[2:])
    return files


# ── Patch gate ────────────────────────────────────────────────────────────────


def check_patch_gate(
    run_metadata: RunMetadata,
    patch_diff: str,
    workspace_mgr: Optional["WorkspaceManager"] = None,
) -> RiskGateResult:
    reasons: list[str] = []

    file_count = len(parse_diff_files(patch_diff))
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
