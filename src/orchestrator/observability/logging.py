from __future__ import annotations

import logging
from pathlib import Path


def get_file_logger(
    name: str,
    logs_dir: Path | None = None,
    filename: str | None = None,
    *,
    level: int = logging.DEBUG,
    fallback_dir: Path | None = None,
    formatter: logging.Formatter | None = None,
) -> logging.Logger:
    if logs_dir is None:
        logs_dir = fallback_dir or Path("logs")
    if filename is None:
        filename = f"{name}.log"
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(level)
    logger.propagate = False

    handler = logging.FileHandler(logs_dir / filename, encoding="utf-8")
    if formatter is None:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger
