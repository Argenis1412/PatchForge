import json
import uuid
from pathlib import Path

from orchestrator.observability.events import log_event
from orchestrator.observability.logger import log_call

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

        config = TargetConfig(target_path=tmp_path, workspace_path=tmp_path)
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

        workspace = tmp_path / "workspace"
        config = TargetConfig(target_path=tmp_path, workspace_path=workspace)
        pipeline = Pipeline(config=config)

        pipeline.execute(dry_run=True)

        f = workspace / "logs" / "pipeline.jsonl"
        assert f.exists()
        lines = [json.loads(line) for line in f.read_text().strip().splitlines()]
        events = [e["event"] for e in lines]
        assert "pipeline_start" in events
        assert "pipeline_end" in events
