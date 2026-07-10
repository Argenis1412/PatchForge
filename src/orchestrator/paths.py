"""Centralized project-root resolution.

Import the module, not the symbol, so monkeypatch is effective::

    from orchestrator import paths
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)

The monkeypatch MUST occur before any code that reads
``paths.PROJECT_ROOT`` (typically before calling ``executor.run()``).
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", str(Path(__file__).resolve().parent.parent)))
