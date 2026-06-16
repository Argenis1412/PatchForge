"""Executor-specific file logger."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from orchestrator.observability.logging import get_file_logger

_logger = None


def _get_logger(logs_dir: Optional[Path] = None):
    global _logger
    if logs_dir is not None or _logger is None:
        _logger = get_file_logger("executor", logs_dir, "executor.log")
        logging.getLogger("httpx").setLevel(logging.WARNING)
    return _logger
