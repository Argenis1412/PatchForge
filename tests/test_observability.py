import json
import logging
import uuid
from pathlib import Path

from orchestrator.observability.events import FailureType, log_event, log_failure
from orchestrator.observability.logger import log_call
from orchestrator.observability.logging import get_file_logger

# ---------------------------------------------------------------------------
# log_event tests
# ---------------------------------------------------------------------------

class TestLogEvent:
    def test_creates_pipeline_jsonl(self, tmp_path: Path) -> None:
        log_event(
            trace_id="t1", run_id="r1", event="pipeline_start",
            logs_dir=tmp_path,
        )
        f = tmp_path / "pipeline.jsonl"
        assert f.exists()
        assert f.read_text().strip() != ""

    def test_schema(self, tmp_path: Path) -> None:
        trace_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        log_event(
            trace_id=trace_id,
            run_id=run_id,
            level="warning",
            source="pipeline",
            stage="scout",
            event="stage_start",
            data={"key": "val"},
            logs_dir=tmp_path,
        )
        line = json.loads((tmp_path / "pipeline.jsonl").read_text())
        assert line["trace_id"] == trace_id
        assert line["run_id"] == run_id
        assert line["level"] == "warning"
        assert line["source"] == "pipeline"
        assert line["stage"] == "scout"
        assert line["event"] == "stage_start"
        assert line["data"] == {"key": "val"}


# ---------------------------------------------------------------------------
# log_call tests
# ---------------------------------------------------------------------------

class TestLogCall:
    def test_backward_compatible_writes_agent_log(self, tmp_path: Path) -> None:
        log_call(
            agent="test_agent",
            prompt="hello",
            response="world",
            tokens={"input": 10, "output": 20},
            cost_usd=0.001,
            logs_dir=tmp_path,
        )
        legacy = tmp_path / "test_agent.log"
        assert legacy.exists()
        entry = json.loads(legacy.read_text())
        assert entry["agent"] == "test_agent"
        assert entry["prompt_chars"] == 5
        assert entry["response_chars"] == 5
        assert entry["tokens"] == {"input": 10, "output": 20}
        assert entry["cost_usd"] == 0.001

    def test_writes_llm_calls_jsonl(self, tmp_path: Path) -> None:
        log_call(
            agent="test_agent",
            prompt="hi",
            response="there",
            tokens={"input": 5, "output": 10},
            cost_usd=0.0,
            logs_dir=tmp_path,
            trace_id="trace-1",
            run_id="run-1",
            stage="scout",
            span_id="pass1",
            model="gemini-2.5-flash",
            latency_ms=1234,
        )
        f = tmp_path / "llm_calls.jsonl"
        assert f.exists()
        entry = json.loads(f.read_text())
        assert entry["trace_id"] == "trace-1"
        assert entry["run_id"] == "run-1"
        assert entry["event"] == "llm_call"
        assert entry["source"] == "agent"
        assert entry["stage"] == "scout"
        assert entry["span_id"] == "pass1"
        assert entry["level"] == "info"
        assert entry["data"]["agent"] == "test_agent"
        assert entry["data"]["model"] == "gemini-2.5-flash"
        assert entry["data"]["latency_ms"] == 1234

    def test_without_trace_still_writes_llm_jsonl(self, tmp_path: Path) -> None:
        log_call(
            agent="no_trace", prompt="a", response="b",
            tokens={"input": 0, "output": 0}, cost_usd=0.0,
            logs_dir=tmp_path,
        )
        f = tmp_path / "llm_calls.jsonl"
        assert f.exists()
        entry = json.loads(f.read_text())
        assert entry["trace_id"] is None
        assert entry["run_id"] is None
        assert entry["event"] == "llm_call"

    def test_with_error_sets_level_error(self, tmp_path: Path) -> None:
        log_call(
            agent="fail", prompt="a", response="",
            tokens={"input": 0, "output": 0}, cost_usd=0.0,
            logs_dir=tmp_path,
            error="timeout after 60s",
        )
        entry = json.loads((tmp_path / "llm_calls.jsonl").read_text())
        assert entry["level"] == "error"
        assert entry["data"]["error"] == "timeout after 60s"


# ---------------------------------------------------------------------------
# Pipeline integration tests
# ---------------------------------------------------------------------------

class TestPipelineObservability:
    def test_trace_id_is_valid_uuid(self, tmp_path: Path) -> None:
        from orchestrator.pipeline import Pipeline
        from orchestrator.schemas.config import TargetConfig

        workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
        config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
        pipeline = Pipeline(config=config)
        assert isinstance(pipeline.trace_id, str)
        uuid.UUID(pipeline.trace_id)

    def test_pipeline_logs_pipeline_start(self, tmp_path: Path, monkeypatch) -> None:
        from orchestrator.pipeline import Pipeline
        from orchestrator.schemas.config import TargetConfig

        # Mock agent runs so pipeline doesn't call LLMs
        class MockArchitectOutput:
            blockers: list = []
            implementation_plan: list = []

        def mock_scout(config, **kw):
            return None, {"cost_usd": 0.0}

        def mock_architect(*a, **kw):
            return MockArchitectOutput(), {"cost_usd": 0.0}

        monkeypatch.setattr("orchestrator.pipeline.run_scout", mock_scout)
        monkeypatch.setattr("orchestrator.pipeline.run_architect", mock_architect)

        workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
        config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
        pipeline = Pipeline(config=config)

        pipeline.execute(dry_run=True)

        f = workspace / "logs" / "pipeline.jsonl"
        assert f.exists()
        lines = [json.loads(line) for line in f.read_text().strip().splitlines()]
        events = [e["event"] for e in lines]
        assert "pipeline_start" in events
        assert "pipeline_end" in events


# ---------------------------------------------------------------------------
# log_failure tests
# ---------------------------------------------------------------------------

class TestLogFailure:
    def test_writes_failure_event_to_pipeline_jsonl(self, tmp_path: Path) -> None:
        log_failure(
            trace_id="t1",
            run_id="r1",
            stage="scout",
            error_type=FailureType.LLM_ERROR,
            message="Gemini failed",
            logs_dir=tmp_path,
        )
        f = tmp_path / "pipeline.jsonl"
        assert f.exists()
        entry = json.loads(f.read_text())
        assert entry["event"] == "failure"
        assert entry["level"] == "error"

    def test_schema_contains_error_type_message(self, tmp_path: Path) -> None:
        log_failure(
            trace_id="t2",
            run_id="r2",
            stage="architect",
            error_type=FailureType.PIPELINE_ABORT,
            message="architect raised blockers",
            data={"blockers": ["missing deps"]},
            logs_dir=tmp_path,
        )
        entry = json.loads((tmp_path / "pipeline.jsonl").read_text())
        assert entry["trace_id"] == "t2"
        assert entry["run_id"] == "r2"
        assert entry["stage"] == "architect"
        assert entry["level"] == "error"
        assert entry["event"] == "failure"
        assert entry["data"]["error_type"] == "pipeline_abort"
        assert entry["data"]["message"] == "architect raised blockers"
        assert entry["data"]["blockers"] == ["missing deps"]

    def test_accepts_string_error_type(self, tmp_path: Path) -> None:
        log_failure(
            trace_id="t3",
            run_id="r3",
            stage="executor",
            error_type="custom_error",
            message="something went wrong",
            logs_dir=tmp_path,
        )
        entry = json.loads((tmp_path / "pipeline.jsonl").read_text())
        assert entry["data"]["error_type"] == "custom_error"

    def test_writes_only_to_pipeline_jsonl_not_llm_calls(self, tmp_path: Path) -> None:
        log_failure(
            trace_id="t4",
            run_id="r4",
            stage="validator",
            error_type=FailureType.TOOL_ERROR,
            message="ruff failed",
            logs_dir=tmp_path,
        )
        assert (tmp_path / "pipeline.jsonl").exists()
        assert not (tmp_path / "llm_calls.jsonl").exists()


class TestPipelineFailureIntegration:
    def test_stage_failure_logs_structured_failure(self, tmp_path: Path, monkeypatch) -> None:
        from orchestrator.pipeline import Pipeline
        from orchestrator.schemas.config import TargetConfig

        def mock_scout(config, **kw):
            raise RuntimeError("LLM call timed out")

        monkeypatch.setattr("orchestrator.pipeline.run_scout", mock_scout)

        workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
        config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
        pipeline = Pipeline(config=config)
        pipeline.execute()

        f = workspace / "logs" / "pipeline.jsonl"
        assert f.exists()
        lines = [json.loads(line) for line in f.read_text().strip().splitlines()]
        failure_events = [e for e in lines if e["event"] == "failure"]
        assert len(failure_events) >= 1
        fe = failure_events[-1]
        assert fe["level"] == "error"
        assert fe["data"]["error_type"] == "pipeline_abort"
        assert "scout failed" in fe["data"]["message"]
        assert fe["stage"] == "scout"

    def test_architect_blockers_logs_structured_failure(self, tmp_path: Path, monkeypatch) -> None:
        from orchestrator.pipeline import Pipeline
        from orchestrator.schemas.architect_output import ArchitectOutput
        from orchestrator.schemas.config import TargetConfig
        from orchestrator.schemas.scout_output import ScoutOutput

        def mock_scout(config, **kw):
            return ScoutOutput(hotspots=[], summary="s", risks=["r"], recommended_order=[]), {"cost_usd": 0.0}

        def mock_architect(*a, **kw):
            output = ArchitectOutput(
                validated_findings=[],
                false_positives=[],
                systemic_risks=[],
                implementation_plan=[],
                blockers=["Missing test command"],
            )
            return output, {"cost_usd": 0.0}

        monkeypatch.setattr("orchestrator.pipeline.run_scout", mock_scout)
        monkeypatch.setattr("orchestrator.pipeline.run_architect", mock_architect)

        workspace = tmp_path.parent / f"{tmp_path.name}-workspace"
        config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
        pipeline = Pipeline(config=config)
        pipeline.execute()

        f = workspace / "logs" / "pipeline.jsonl"
        assert f.exists()
        lines = [json.loads(line) for line in f.read_text().strip().splitlines()]
        failure_events = [e for e in lines if e["event"] == "failure"]
        assert len(failure_events) >= 1
        fe = failure_events[-1]
        assert fe["level"] == "error"
        assert fe["data"]["error_type"] == "pipeline_abort"
        assert "blockers" in fe["data"]
        assert fe["data"]["blockers"] == ["Missing test command"]


# ---------------------------------------------------------------------------
# get_file_logger tests
# ---------------------------------------------------------------------------

class TestGetFileLogger:
    def test_creates_log_file(self, tmp_path: Path) -> None:
        logger = get_file_logger("test_agent", tmp_path, "test.log")
        assert logger.name == "test_agent"
        assert (tmp_path / "test.log").exists()

    def test_default_filename_from_name(self, tmp_path: Path) -> None:
        logger = get_file_logger("myagent", tmp_path)
        assert logger.name == "myagent"
        assert (tmp_path / "myagent.log").exists()

    def test_uses_fallback_logs_dir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        get_file_logger("fallback_test")
        assert (tmp_path / "logs" / "fallback_test.log").exists()

    def test_custom_fallback_dir(self, tmp_path: Path) -> None:
        fb = tmp_path / "custom_fb"
        get_file_logger("fb_test", fallback_dir=fb)
        assert (fb / "fb_test.log").exists()

    def test_closes_and_replaces_handlers(self, tmp_path: Path) -> None:
        logger = get_file_logger("replace_test", tmp_path, "a.log")
        assert len(logger.handlers) == 1
        h1 = logger.handlers[0]

        logger2 = get_file_logger("replace_test", tmp_path, "b.log")
        assert len(logger2.handlers) == 1
        assert h1 is not logger2.handlers[0]
        assert h1.stream is None

    def test_sets_propagate_false(self, tmp_path: Path) -> None:
        logger = get_file_logger("prop_test", tmp_path, "p.log")
        assert logger.propagate is False

    def test_custom_formatter(self, tmp_path: Path) -> None:
        fmt = logging.Formatter("%(name)s: %(message)s")
        logger = get_file_logger("fmt_test", tmp_path, "f.log", formatter=fmt)
        logger.info("hello")
        content = (tmp_path / "f.log").read_text()
        assert content.startswith("fmt_test:")
        assert "hello" in content

    def test_default_level_is_debug(self, tmp_path: Path) -> None:
        logger = get_file_logger("lvl_test", tmp_path, "l.log")
        assert logger.level == logging.DEBUG
