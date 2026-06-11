"""
PatchForge exception hierarchy.

All PatchForge-specific exceptions inherit from PatchForgeError.
This module is the single canonical location for exception class definitions.

Exceptions defined here:
- PatchForgeError     — base for all PatchForge errors
- SchemaVersionError  — schema version mismatch on artifact load
- ProviderError       — LLM provider failure (Anthropic, Gemini)

Parser exceptions (LLMParseError, SchemaValidationError) are defined in
orchestrator/llm/parser.py and also inherit from PatchForgeError.

PipelineAbortError is defined in pipeline.py and also inherits from
PatchForgeError.
"""


class PatchForgeError(Exception):
    """Base exception for all PatchForge errors."""


class SchemaVersionError(PatchForgeError):
    """Raised when a persisted artifact's schema version does not match
    the current software's expected version.

    Attributes:
        found: The schema_version read from the artifact.
        expected: The CURRENT_SCHEMA_VERSION defined in run_metadata.py.
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
