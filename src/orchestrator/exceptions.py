"""
PatchForge exception hierarchy.

All PatchForge-specific exceptions inherit from PatchForgeError.
This module is the single canonical location for exception class definitions.

Exceptions defined here:
- PatchForgeError          — base for all PatchForge errors
- SchemaVersionError       — schema version mismatch on artifact load
- ProviderError            — LLM provider failure (Anthropic, Gemini)
- RollbackError            — git rollback failure (T-02)
- PathSafetyError          — path traversal / escape from base directory (T-01)
- CircuitBreakerOpenError  — call rejected by an OPEN circuit breaker (T-07B)

Parser exceptions (LLMParseError, SchemaValidationError) are defined in
orchestrator/llm/parser.py and also inherit from PatchForgeError.

PipelineAbortError is defined in pipeline.py and also inherits from
PatchForgeError.
"""

from pathlib import Path


class PatchForgeError(Exception):
    """Base exception for all PatchForge errors."""


class SchemaVersionError(PatchForgeError):
    """Raised when a persisted artifact's schema version does not match
    the current software's expected version.

    Attributes:
        found: The schema_version read from the artifact.
        expected: The CURRENT_SCHEMA_VERSION defined in artifacts.py.
    """

    def __init__(self, *, found: int, expected: int) -> None:
        self.found = found
        self.expected = expected
        super().__init__(f"Schema version mismatch: artifact has {found}, expected {expected}")


class ProviderError(PatchForgeError):
    """Raised when an LLM provider call fails unrecoverably.

    Attributes:
        provider: Name of the provider (e.g. 'anthropic', 'gemini').
    """

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class RollbackError(PatchForgeError):
    """Raised when a rollback operation fails unrecoverably.

    Attributes:
        repo_root: Path to the repository that failed to roll back.
        target_sha: The commit SHA that the rollback attempted to reach.
        stderr: stderr output from the failed git commands.
    """

    def __init__(self, repo_root: Path, target_sha: str, stderr: str) -> None:
        self.repo_root = repo_root
        self.target_sha = target_sha
        self.stderr = stderr
        super().__init__(f"Rollback failed for {repo_root} to {target_sha}: {stderr}")


class PathSafetyError(PatchForgeError):
    """Raised when a file path escapes its intended base directory.

    Attributes:
        path: The user-supplied path string that caused the violation.
        base: The base directory the path was validated against.
    """

    def __init__(self, *, path: str, base: Path) -> None:
        self.path = path
        self.base = base
        super().__init__(f"Path safety violation: {path!r} escapes base {base}")


class CycleDetectedError(PatchForgeError):
    """Raised when a dependency cycle is detected in the task DAG.

    Attributes:
        cycle_tasks: The tasks involved in the cycle (in resolution order).
    """

    def __init__(self, cycle_tasks: list[str]) -> None:
        self.cycle_tasks = cycle_tasks
        super().__init__(f"Dependency cycle detected: {' \u2192 '.join(cycle_tasks)}")


class SchedulerInvariantError(PatchForgeError):
    """Raised when the scheduler encounters an invariant violation.

    Examples: a task depends on a non-existent task_id, or a task that was
    expected in results is missing.
    """


class PatchApplyError(PatchForgeError):
    """Raised when a patch cannot be applied (deterministic failure).

    Covers `git apply` non-zero returns (conflict, malformed patch, missing
    file) and risk-gate blocks after the executor materializes the diff.
    Listed in the worker loop's DETERMINISTIC_EXCEPTIONS so it routes to
    dead_letter instead of retrying.
    """


class GitConflictError(PatchForgeError):
    """Raised when a git operation reports a deterministic conflict
    (e.g. branch creation against an existing branch, checkout against a
    divergent tree). Listed in DETERMINISTIC_EXCEPTIONS."""


class CircuitBreakerOpenError(PatchForgeError):
    """Raised when a call is rejected by an OPEN circuit breaker.

    NOT ProviderError — avoids capture by existing except ProviderError handlers
    in agent retry loops, which would defeat the fail-fast behavior.

    Attributes:
        provider: Name of the provider whose CB is open (e.g. 'gemini').
        state: The CircuitBreakerState at time of rejection.
        retry_after: time.monotonic() timestamp after which a probe is allowed.
        message: Optional human-readable context.
    """

    def __init__(
        self,
        provider: str,
        state: object,
        retry_after: float,
        message: str = "",
    ) -> None:
        self.provider = provider
        self.state = state
        self.retry_after = retry_after
        self.message = message
        detail = message or "circuit breaker is open"
        super().__init__(f"[{provider}] {detail}")
