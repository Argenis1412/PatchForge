from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.artifacts import RunMetadata
from orchestrator.schemas.risk import RiskGateResult


def check_plan_gate(
    run_metadata: RunMetadata,
    architect_output: ArchitectOutput,
) -> RiskGateResult:
    reasons: list[str] = []
    budget = run_metadata.risk_budget

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

    return RiskGateResult(
        passed=len(reasons) == 0,
        gate="plan",
        reasons=reasons,
    )


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
            parts = line.split()
            if len(parts) >= 4:
                b_path = parts[3]
                if b_path.startswith("b/"):
                    files.add(b_path[2:])
    return len(files)


def check_patch_gate(
    run_metadata: RunMetadata,
    patch_diff: str,
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

    return RiskGateResult(
        passed=len(reasons) == 0,
        gate="patch",
        reasons=reasons,
    )
