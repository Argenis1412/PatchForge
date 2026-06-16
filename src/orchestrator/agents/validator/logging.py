"""Validator-specific file logger."""

from pathlib import Path

_logger = None


def _get_logger(logs_dir: Path | None = None):
    global _logger
    if logs_dir is not None or _logger is None:
        from orchestrator.agents.validator import get_file_logger

        _logger = get_file_logger("validator", logs_dir, "validator.log")
    return _logger
