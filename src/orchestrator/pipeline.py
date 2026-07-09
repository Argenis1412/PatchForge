from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from orchestrator.agents.architect import run as run_architect
from orchestrator.agents.executor import run as run_executor
from orchestrator.agents.scout import run as run_scout
from orchestrator.agents.validator import run as run_validator
from orchestrator.exceptions import PatchForgeError, SchemaVersionError
from orchestrator.observability.events import FailureType, log_event, log_failure
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.artifacts import CURRENT_SCHEMA_VERSION
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput
from orchestrator.schemas.pipeline_run import AgentMeta, PipelineRun, TaskResult
from orchestrator.schemas.scout_output import ScoutOutput
from orchestrator.workspace import WorkspaceManager


class PipelineAbortError(PatchForgeError):
    """Raised when a stage fails and downstream stages must not execute."""

    def __init__(
        self,
        message: str,
        error_type: FailureType = FailureType.PIPELINE_ABORT,
        stage: str | None = None,
        data: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.stage = stage
        self.data = data or {}


class Pipeline:
    def __init__(self, config: TargetConfig, from_stage: str | None = None) -> None:
        self.config = config
        self.target_path = config.target_path
        self.run = PipelineRun(target_path=str(self.target_path))
        self.from_stage = from_stage
        self.trace_id = str(uuid.uuid4())
        self.workspace = WorkspaceManager(self.config.workspace_path)
        self.workspace.setup()

    def _log_event(
        self,
        event: str,
        *,
        level: str = "info",
        source: str = "pipeline",
        stage: str | None = None,
        data: dict | None = None,
    ) -> None:
        log_event(
            trace_id=self.trace_id,
            run_id=self.run.run_id,
            level=level,
            source=source,
            stage=stage,
            event=event,
            data=data,
            logs_dir=self.config.workspace_path / "logs",
        )
        # Keep stdout for current UX
        print(
            json.dumps(
                {
                    "event": event,
                    "ts": datetime.utcnow().isoformat(),
                    **({"data": data} if data else {}),
                }
            )
        )

    def _log_failure(
        self,
        error_type: FailureType | str,
        message: str,
        *,
        stage: str | None = None,
        source: str = "pipeline",
        retry_count: int | None = None,
        duration_ms: int | None = None,
        data: dict | None = None,
    ) -> None:
        log_failure(
            trace_id=self.trace_id,
            run_id=self.run.run_id,
            stage=stage,
            error_type=error_type,
            message=message,
            source=source,
            retry_count=retry_count,
            duration_ms=duration_ms,
            data=data,
            logs_dir=self.config.workspace_path / "logs",
        )
        error_type_value = error_type if isinstance(error_type, str) else error_type.value
        payload = {"error_type": error_type_value, "message": message}
        if data and "blockers" in data:
            payload["blockers"] = data["blockers"]
        print(
            json.dumps({"event": "failure", "ts": datetime.utcnow().isoformat(), "data": payload})
        )

    def execute(self, dry_run: bool = False) -> PipelineRun:
        self._log_event("pipeline_start", data={"target": str(self.target_path)})
        t0_pipeline = time.monotonic()

        try:
            loaded = self.workspace.read_run_json(self.run.run_id)
        except FileNotFoundError:
            pass
        else:
            if loaded.schema_version != CURRENT_SCHEMA_VERSION:
                raise SchemaVersionError(
                    found=loaded.schema_version,
                    expected=CURRENT_SCHEMA_VERSION,
                )

        try:
            scout_output = None
            architect_output = None

            # ── Stage: Scout ────────────────────────────────────────────────
            if self.from_stage is None:
                scout_output = self._stage_scout()
            else:
                self._log_event(
                    "stage_end",
                    stage="scout",
                    level="warning",
                    data={"reason": f"starting from {self.from_stage}"},
                )

            # ── Stage: Architect ────────────────────────────────────────────
            if self.from_stage in [None, "scout"]:
                # scout_output comes from stage_scout (or another path)
                # Ensure scout_output exists if not loading from stage
                if scout_output is None:
                    scout_output = self._load_stage_output(ScoutOutput, "scout")
                architect_output = self._stage_architect(scout_output)
            elif self.from_stage == "architect":
                architect_output = self._load_stage_output(ArchitectOutput, "architect")

            if dry_run:
                self._log_event("pipeline_end", data={"reason": "dry_run"})
                self.run.status = "completed"
                return self.run

            # ── Stage: Executor ─────────────────────────────────────────────
            if self.from_stage in [None, "scout", "architect"]:
                self._stage_executor(architect_output)
            elif self.from_stage == "executor":
                executor_result = self._load_stage_output(ExecutorOutput, "executor")
                self._apply_executor_results(executor_result, model_used=executor_result.model)

            # ── Stage: Validator ────────────────────────────────────────────
            self._stage_validator()

        except PipelineAbortError as exc:
            self.run.status = "failed"
            self._log_failure(
                error_type=exc.error_type,
                message=str(exc),
                stage=exc.stage,
                duration_ms=_ms(t0_pipeline),
                data=exc.data,
            )
            # Populate failure_artifacts on RunMetadata if it exists
            self._populate_failure_artifacts(exc.stage)
        except Exception as exc:
            self.run.status = "failed"
            self._log_failure(
                error_type=FailureType.UNKNOWN,
                message=str(exc),
                duration_ms=_ms(t0_pipeline),
            )
            raise

        else:
            self.run.status = self._final_status()

        finally:
            self.run.finished_at = datetime.utcnow()
            self.run.total_cost_usd = _sum_costs(self.run)
            self._persist()

        self._log_event(
            "pipeline_end", data={"status": self.run.status, "cost_usd": self.run.total_cost_usd}
        )
        return self.run

    def _load_stage_output(self, model_class, stage: str):
        manifest = self.workspace.read_manifest()
        filename = manifest.get("latest", {}).get(stage)
        if not filename:
            raise PipelineAbortError(
                f"No previous output found for stage {stage} in manifest",
                stage=stage,
                data={"stage": stage},
            )
        path = self.workspace.outputs / filename
        if not path.exists():
            raise PipelineAbortError(
                f"Manifest points to {filename} but file does not exist",
                stage=stage,
                data={"stage": stage, "filename": filename},
            )
        try:
            return model_class.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as e:
            raise PipelineAbortError(
                f"Failed to load {stage} output: {e}. Re-run from an earlier stage.",
                stage=stage,
                data={"stage": stage},
            ) from e

    def _persist_stage_output(self, stage: str, output) -> None:
        filename = f"{stage}_{self.run.run_id}.json"
        path = self.workspace.outputs / filename
        path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
        self.workspace.update_manifest(stage, filename)

    def _stage_scout(self) -> ScoutOutput:
        self._log_event("stage_start", stage="scout")
        t0 = time.monotonic()
        try:
            output, meta = run_scout(self.config, trace_id=self.trace_id, run_id=self.run.run_id)
            self.run.scout_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            self._persist_stage_output("scout", output)
            self._log_event("stage_end", stage="scout", data={"cost_usd": meta.get("cost_usd")})
            return output
        except Exception as exc:
            self.run.scout_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbortError(f"scout failed: {exc}", stage="scout") from exc

    def _stage_architect(self, scout_output: ScoutOutput) -> ArchitectOutput:
        self._log_event("stage_start", stage="architect")
        t0 = time.monotonic()
        try:
            output, meta = run_architect(
                scout_output, config=self.config, trace_id=self.trace_id, run_id=self.run.run_id
            )
            self.run.architect_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            self._persist_stage_output("architect", output)
            blockers = output.blockers
            self._log_event(
                "stage_end",
                stage="architect",
                data={"cost_usd": meta.get("cost_usd"), "blockers": blockers},
            )
            if blockers:
                raise PipelineAbortError(
                    f"architect raised blockers: {blockers}",
                    stage="architect",
                    data={"blockers": blockers},
                )
            return output
        except PipelineAbortError:
            raise
        except Exception as exc:
            self.run.architect_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbortError(f"architect failed: {exc}", stage="architect") from exc

    def _apply_executor_results(self, result: ExecutorOutput, model_used: str = "unknown") -> None:
        self.run.tasks_total = len(result.applied) + len(result.pending_review) + len(result.errors)
        for change in result.applied:
            self.run.task_results.append(
                TaskResult(
                    task_id=change.task_id,
                    status="applied",
                    risk_level="low",
                    model_used=model_used,
                )
            )
            self.run.tasks_applied += 1
        for change in result.pending_review:
            self.run.task_results.append(
                TaskResult(
                    task_id=change.task_id,
                    status="diff_pending_review",
                    risk_level="high",
                    model_used=model_used,
                )
            )
            self.run.pending_human_review.append(change.diff)
            self.run.tasks_pending_review += 1
        for change in result.errors:
            self.run.task_results.append(
                TaskResult(
                    task_id=change.task_id,
                    status="failed",
                    risk_level="low",
                    model_used=model_used,
                    error=change.error,
                )
            )
            self.run.tasks_failed += 1

    def _stage_executor(self, architect_output: ArchitectOutput) -> None:
        self._log_event("stage_start", stage="executor")
        t0 = time.monotonic()
        try:
            staging_dir = self.workspace.staging_dir_for_run(self.run.run_id)
            result, meta = run_executor(
                architect_output,
                run_id=self.run.run_id,
                config=self.config,
                staging_dir=staging_dir,
                logs_dir=self.config.workspace_path / "logs",
                run_dir=self.workspace.run_dir(self.run.run_id),
                trace_id=self.trace_id,
            )
            self.run.executor_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            self._persist_stage_output("executor", result)
            self._apply_executor_results(result, model_used=meta.get("model_used", "unknown"))
            self._log_event(
                "stage_end",
                stage="executor",
                data={"cost_usd": meta.get("cost_usd"), "tasks_applied": self.run.tasks_applied},
            )
        except Exception as exc:
            self.run.executor_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbortError(f"executor failed: {exc}", stage="executor") from exc

    def _stage_validator(self) -> None:
        if self.run.tasks_applied == 0:
            self._log_event(
                "stage_end", stage="validator", level="warning", data={"reason": "no tasks applied"}
            )
            self.run.validator_meta = AgentMeta(status="skipped", latency_ms=0)
            return
        self._log_event("stage_start", stage="validator")
        t0 = time.monotonic()
        try:
            staging_dir = self.workspace.staging_dir_for_run(self.run.run_id)
            result, meta = run_validator(config=self.config, staging_dir=staging_dir)
            status = "success" if result.overall_passed else "failed"
            self.run.validator_meta = AgentMeta(status=status, latency_ms=_ms(t0), **meta)
            self._persist_stage_output("validator", result)
        except Exception as exc:
            self.run.validator_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            self._log_failure(
                FailureType.TOOL_ERROR,
                f"validator failed: {exc}",
                stage="validator",
                duration_ms=_ms(t0),
            )

    def _final_status(self) -> str:
        if self.run.validator_meta and self.run.validator_meta.status == "failed":
            return "validation_failed"
        if self.run.pending_human_review:
            return "awaiting_review"
        return "completed"

    def _populate_failure_artifacts(self, stage: str | None) -> None:
        try:
            run_metadata = self.workspace.read_run_json(self.run.run_id)
            failure_artifact = f"{stage}_failure.json" if stage else "pipeline_failure.json"
            if run_metadata.failure_artifacts is None:
                run_metadata.failure_artifacts = []
            if failure_artifact not in run_metadata.failure_artifacts:
                run_metadata.failure_artifacts.append(failure_artifact)
            run_metadata.status = "failed"
            run_metadata.updated_at = datetime.now(timezone.utc)
            self.workspace.write_run_json(self.run.run_id, run_metadata)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "failure_artifacts_skipped",
                        "stage": stage,
                        "reason": str(exc),
                    }
                )
            )

    def _persist(self) -> None:
        path = self.workspace.runs / f"pipeline_{self.run.run_id}.json"
        path.write_text(self.run.model_dump_json(indent=2), encoding="utf-8")


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _sum_costs(run: PipelineRun) -> float:
    metas = [run.scout_meta, run.architect_meta, run.executor_meta, run.validator_meta]
    return round(sum(m.cost_usd for m in metas if m and m.cost_usd), 6)
