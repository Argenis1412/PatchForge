from __future__ import annotations

import json
import time
from datetime import datetime

from orchestrator.agents.architect import run as run_architect
from orchestrator.agents.executor import run as run_executor
from orchestrator.agents.scout import run as run_scout
from orchestrator.agents.validator import run as run_validator
from orchestrator.schemas.architect_output import ArchitectOutput
from orchestrator.schemas.config import TargetConfig
from orchestrator.schemas.pipeline_run import AgentMeta, PipelineRun, TaskResult
from orchestrator.schemas.scout_output import ScoutOutput


class PipelineAbort(RuntimeError):
    """Raised when a stage fails and downstream stages must not execute."""

class Pipeline:
    def __init__(self, config: TargetConfig, from_stage: str | None = None) -> None:
        self.config = config
        self.target_path = config.target_path
        self.run = PipelineRun(target_path=str(self.target_path))
        self.from_stage = from_stage
        
        # Ensure directories exist
        self.config.workspace_path.mkdir(parents=True, exist_ok=True)
        (self.config.workspace_path / "logs").mkdir(exist_ok=True)
        (self.config.workspace_path / "runs").mkdir(exist_ok=True)

    def execute(self, dry_run: bool = False) -> PipelineRun:
        _log("pipeline.start", run_id=self.run.run_id, target=str(self.target_path))

        try:
            scout_output = None
            architect_output = None

            # ── Stage: Scout ────────────────────────────────────────────────
            if self.from_stage is None:
                scout_output = self._stage_scout()
            else:
                _log("scout.skip", reason=f"starting from {self.from_stage}")

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
                _log("pipeline.dry_run", reason="stopping after architect")
                self.run.status = "completed"
                return self.run

            # ── Stage: Executor ─────────────────────────────────────────────
            if self.from_stage in [None, "scout", "architect"]:
                self._stage_executor(architect_output)
            elif self.from_stage == "executor":
                # Executor doesn't have a simple output schema to re-run from
                # We assume validator runs after execution.
                pass

            # ── Stage: Validator ────────────────────────────────────────────
            self._stage_validator()

        except PipelineAbort as exc:
            self.run.status = "failed"
            _log("pipeline.abort", run_id=self.run.run_id, reason=str(exc))
        except Exception as exc:
            self.run.status = "failed"
            _log("pipeline.error", error=str(exc))
            raise

        else:
            self.run.status = self._final_status()

        finally:
            self.run.finished_at = datetime.utcnow()
            self.run.total_cost_usd = _sum_costs(self.run)
            self._persist()

        _log("pipeline.finish", run_id=self.run.run_id, status=self.run.status, cost_usd=self.run.total_cost_usd)
        return self.run

    def _load_stage_output(self, model_class, stage: str):
        # Look for most recent log file for this stage
        logs_dir = self.config.workspace_path / "logs"
        files = sorted(logs_dir.glob(f"{stage}_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if not files:
            raise PipelineAbort(f"No previous output found for stage {stage} in {logs_dir}")
        try:
            return model_class.model_validate_json(files[0].read_text())
        except Exception as e:
            raise PipelineAbort(f"Failed to load {stage} output: {e}. Re-run from an earlier stage.")

    def _stage_scout(self) -> ScoutOutput:
        _log("scout.start", run_id=self.run.run_id)
        t0 = time.monotonic()
        try:
            output, meta = run_scout(self.config)
            self.run.scout_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            _log("scout.done", cost=meta.get("cost_usd"))
            return output
        except Exception as exc:
            self.run.scout_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbort(f"scout failed: {exc}")

    def _stage_architect(self, scout_output: ScoutOutput) -> ArchitectOutput:
        _log("architect.start", run_id=self.run.run_id)
        t0 = time.monotonic()
        try:
            output, meta = run_architect(scout_output, config=self.config)
            self.run.architect_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            if output.blockers:
                raise PipelineAbort(f"architect raised blockers: {output.blockers}")
            return output
        except PipelineAbort:
            raise
        except Exception as exc:
            self.run.architect_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbort(f"architect failed: {exc}")

    def _stage_executor(self, architect_output: ArchitectOutput) -> None:
        _log("executor.start", run_id=self.run.run_id)
        t0 = time.monotonic()
        try:
            result, meta = run_executor(architect_output, config=self.config)
            self.run.executor_meta = AgentMeta(status="success", latency_ms=_ms(t0), **meta)
            self.run.tasks_total = len(architect_output.implementation_plan)
            # Map executor results to run results
            for change in result.applied:
                self.run.task_results.append(TaskResult(task_id=change.task_id, status="applied", risk_level="low", model_used=meta.get("model_used", "unknown")))
                self.run.tasks_applied += 1
            for change in result.pending_review:
                self.run.task_results.append(TaskResult(task_id=change.task_id, status="diff_pending_review", risk_level="high", model_used=meta.get("model_used", "unknown")))
                self.run.pending_human_review.append(change.diff)
                self.run.tasks_pending_review += 1
            for change in result.errors:
                self.run.task_results.append(TaskResult(task_id=change.task_id, status="failed", risk_level="low", model_used=meta.get("model_used", "unknown"), error=change.error))
                self.run.tasks_failed += 1
        except Exception as exc:
            self.run.executor_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            raise PipelineAbort(f"executor failed: {exc}")

    def _stage_validator(self) -> None:
        if self.run.tasks_applied == 0:
            _log("validator.skip", reason="no tasks applied")
            self.run.validator_meta = AgentMeta(status="skipped", latency_ms=0)
            return
        _log("validator.start", run_id=self.run.run_id)
        t0 = time.monotonic()
        try:
            result, meta = run_validator(config=self.config)
            self.run.validator_meta = AgentMeta(status="success" if result.overall_passed else "failed", latency_ms=_ms(t0), **meta)
        except Exception as exc:
            self.run.validator_meta = AgentMeta(status="failed", error=str(exc), latency_ms=_ms(t0))
            _log("validator.error", error=str(exc))

    def _final_status(self) -> str:
        if self.run.validator_meta and self.run.validator_meta.status == "failed":
            return "validation_failed"
        if self.run.pending_human_review:
            return "awaiting_review"
        return "completed"

    def _persist(self) -> None:
        path = self.config.workspace_path / "runs" / f"pipeline_{self.run.run_id}.json"
        path.write_text(self.run.model_dump_json(indent=2))

def _ms(t0: float) -> int: return int((time.monotonic() - t0) * 1000)
def _sum_costs(run: PipelineRun) -> float:
    metas = [run.scout_meta, run.architect_meta, run.executor_meta, run.validator_meta]
    return round(sum(m.cost_usd for m in metas if m and m.cost_usd), 6)
def _log(event: str, **kwargs) -> None:
    print(json.dumps({"event": event, "ts": datetime.utcnow().isoformat(), **kwargs}))


