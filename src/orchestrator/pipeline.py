from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from orchestrator.agents.architect import run as run_architect
from orchestrator.agents.executor import run as run_executor
from orchestrator.agents.scout import run as run_scout
from orchestrator.agents.validator import run as run_validator
from orchestrator.observability.events import log_event
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.executor_output import ExecutorOutput
from orchestrator.schemas.pipeline_run import AgentMeta, PipelineRun, TaskResult
from orchestrator.schemas.scout_output import ScoutOutput
from orchestrator.workspace import WorkspaceManager


class PipelineAbort(RuntimeError):
    """Raised when a stage fails and downstream stages must not execute."""

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
        print(json.dumps({"event": event, "ts": datetime.utcnow().isoformat(), **({"data": data} if data else {})}))

    def execute(self, dry_run: bool = False) -> PipelineRun:
        self._log_event("pipeline_start", data={"target": str(self.target_path)})

        try:
            scout_output = None
            architect_output = None

            # ── Stage: Scout ────────────────────────────────────────────────
            if self.from_stage is None:
                scout_output = self._stage_scout()
            else:
                self._log_event("stage_end", stage="scout", level="warning", data={"reason": f"starting from {self.from_stage}"})

            # ── Stage: Architect ────────────────────────────────────────────
            if self.from_stage in [None, "scout"]:
                # scout_output viene de stage_scout (u otro camino)
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

        except PipelineAbort as exc:
            self.run.status = "failed"
            self._log_event("failure", level="error", data={"reason": str(exc)})
        except Exception as exc:
            self.run.status = "failed"
            self._log_event("failure", level="error", data={"error": str(exc)})
            raise

        else:
            self.run.status = self._final_status()

        finally:
            self.run.finished_at = datetime.utcnow()
            self.run.total_cost_usd = _sum_costs(self.run)
            self._persist()

        self._log_event("pipeline_end", data={"status": self.run.status, "cost_usd": self.run.total_cost_usd})
        return self.run

    def _load_stage_output(self, model_class, stage: str):
        outputs_dir = self.workspace.outputs
        files = sorted(outputs_dir.glob(f"{stage}_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            raise PipelineAbort(f"No previous output found for stage {stage} in {outputs_dir}")
        try:
            return model_class.model_validate_json(files[0].read_text())
        except Exception as e:
            raise PipelineAbort(f"Failed to load {stage} output: {e}. Re-run from an earlier stage.")

    def _persist_stage_output(self, stage: str, output) -> None:
        path = self.workspace.outputs / f"{stage}_{self.run.run_id}.json"
        path.write_text(output.model_dump_json(indent=2), encoding="utf-8")

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
            raise PipelineAbort(f"scout failed: {exc}")

    def _stage_architect(self, scout_output: ScoutOutput) -> ArchitectOutput:
        self._log_event("stage_start", stage="architect")
        t0 = time.monotonic()
        try:
            output, meta = run_architect(scout_output, config=self.config, trace_id=self.trace_id, run_id=self.run.run_id)
            self.run.architect_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            self._persist_stage_output("architect", output)
            blockers = output.blockers
            self._log_event("stage_end", stage="architect", data={"cost_usd": meta.get("cost_usd"), "blockers": blockers})
            if blockers:
                raise PipelineAbort(f"architect raised blockers: {blockers}")
            return output
        except PipelineAbort:
            raise
        except Exception as exc:
            self.run.architect_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbort(f"architect failed: {exc}")

    def _apply_executor_results(self, result: ExecutorOutput, model_used: str = "unknown") -> None:
        self.run.tasks_total = len(result.applied) + len(result.pending_review) + len(result.errors)
        for change in result.applied:
            self.run.task_results.append(TaskResult(task_id=change.task_id, status="applied", risk_level="low", model_used=model_used))
            self.run.tasks_applied += 1
        for change in result.pending_review:
            self.run.task_results.append(TaskResult(task_id=change.task_id, status="diff_pending_review", risk_level="high", model_used=model_used))
            self.run.pending_human_review.append(change.diff)
            self.run.tasks_pending_review += 1
        for change in result.errors:
            self.run.task_results.append(TaskResult(task_id=change.task_id, status="failed", risk_level="low", model_used=model_used, error=change.error))
            self.run.tasks_failed += 1

    def _stage_executor(self, architect_output: ArchitectOutput) -> None:
        self._log_event("stage_start", stage="executor")
        t0 = time.monotonic()
        try:
            result, meta = run_executor(architect_output, config=self.config)
            self.run.executor_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            self._persist_stage_output("executor", result)
            self._apply_executor_results(result, model_used=meta.get("model_used", "unknown"))
            self._log_event("stage_end", stage="executor", data={"cost_usd": meta.get("cost_usd"), "tasks_applied": self.run.tasks_applied})
        except Exception as exc:
            self.run.executor_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbort(f"executor failed: {exc}")

    def _stage_validator(self) -> None:
        if self.run.tasks_applied == 0:
            self._log_event("stage_end", stage="validator", level="warning", data={"reason": "no tasks applied"})
            self.run.validator_meta = AgentMeta(status="skipped", latency_ms=0)
            return
        self._log_event("stage_start", stage="validator")
        t0 = time.monotonic()
        try:
            result, meta = run_validator(config=self.config)
            self.run.validator_meta = AgentMeta(status="success" if result.overall_passed else "failed", latency_ms=_ms(t0), **meta)
            self._persist_stage_output("validator", result)
        except Exception as exc:
            self.run.validator_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            self._log_event("failure", level="error", stage="validator", data={"error": str(exc)})

    def _final_status(self) -> str:
        if self.run.validator_meta and self.run.validator_meta.status == "failed":
            return "validation_failed"
        if self.run.pending_human_review:
            return "awaiting_review"
        return "completed"

    def _persist(self) -> None:
        path = self.workspace.runs / f"pipeline_{self.run.run_id}.json"
        path.write_text(self.run.model_dump_json(indent=2))

def _ms(t0: float) -> int: return int((time.monotonic() - t0) * 1000)
def _sum_costs(run: PipelineRun) -> float:
    metas = [run.scout_meta, run.architect_meta, run.executor_meta, run.validator_meta]
    return round(sum(m.cost_usd for m in metas if m and m.cost_usd), 6)


