from pathlib import Path
from typing import Optional

_ENV_LOADED = False


def bootstrap_environment(env_file: Optional[Path] = None, target_path: Optional[Path] = None):
    global _ENV_LOADED
    if _ENV_LOADED and env_file is None:
        return
    from dotenv import load_dotenv

    resolved_path = None

    # 1. CLI / Manual option override
    if env_file is not None:
        p = Path(env_file).resolve()
        if p.exists():
            resolved_path = p

    # 2. Current Working Directory
    if resolved_path is None:
        p = Path.cwd() / ".env"
        if p.exists():
            resolved_path = p

    # 3. Target root directory
    if resolved_path is None and target_path is not None:
        p = Path(target_path).resolve() / ".env"
        if p.exists():
            resolved_path = p

    # 4. Package local fallback (for backwards compatibility / tests)
    if resolved_path is None:
        p = Path(__file__).resolve().parent.parent / ".env"
        if p.exists():
            resolved_path = p

    if resolved_path is not None:
        load_dotenv(resolved_path)

    _ENV_LOADED = True
