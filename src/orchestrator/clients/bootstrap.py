"""Compatibility bootstrap hooks for non-credential runtime setup."""

from __future__ import annotations

from pathlib import Path


def bootstrap_environment(env_file: Path | None = None, target_path: Path | None = None) -> None:
    """Retained compatibility seam; provider credentials are resolved explicitly."""
    del env_file, target_path


def bootstrap_databases(base_dir: Path) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "coordination.db").touch()
    from orchestrator.storage.work_queue import init_queue_db

    conn = init_queue_db(base_dir / "queue.db")
    conn.close()
