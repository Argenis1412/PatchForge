# ADR-004: Runtime Boundaries & Operational Hygiene

## Status
Accepted (Phase 2, May 2026)

## Context
The multi-agent pipeline suffered from import-time side effects: SDK clients were initialized at module load, `load_dotenv()` mutated process state during import, and missing dependencies killed `pytest` collection before any test could run. This made the system fragile, non-portable, and hard to validate in CI.

## Decision
We establish four binding rules for all present and future agent code:

### 1. Imports MUST be side-effect free
Importing any module under `agents/` or `schemas/` MUST NOT:
- Initialize SDK clients (Anthropic, Gemini, Groq, etc.)
- Call `load_dotenv()` or any IO operation
- Read files or environment variables
- Create directories (`mkdir`)
- Set up loggers that write to disk

All modules must be importable in a clean Python process without network, filesystem, or environment access.

### 2. SDK clients MUST be initialized lazily via `clients/`
The `clients/` package provides singleton-lazy factory functions:
- `clients.gemini_client.get_gemini_client()`
- `clients.anthropic_client.get_anthropic_client()`
- `clients.groq_client.get_groq_client()`

Each factory:
- Caches the client in a module-level `_client` variable
- Imports the heavyweight SDK on first call, not at import time
- Reads API keys from `os.environ` at call time (after `bootstrap_environment()` has run)

No agent code may import `anthropic`, `google.genai`, or `httpx` at the top level.

### 3. Environment bootstrap is explicit, never implicit
- `clients/bootstrap.bootstrap_environment()` must be called explicitly before any SDK client is used.
- `load_dotenv()` must NOT appear at module level in any agent.
- The canonical call site is the pipeline entrypoint (`main.py`, `pipeline.py`) or the script's `__main__` block.

### 4. The Validator owns environment health
Before any execution, run:
```bash
python scripts/bootstrap_check.py --strict-env
```
This validates Python version, critical packages, and required environment variables. If it fails, no agent runs.

## Consequences
- `pytest --collect-only` can succeed without API keys or network access
- Tests can mock `get_*_client()` functions instead of monkeypatching SDK internals
- Startup latency shifts from import-time to first-use (negligible for pipeline use)
- All agents follow a uniform, auditable pattern for external dependencies

## Exceptions
- `scripts/bootstrap_check.py` may import SDKs at top level (it is a validation tool)
- `__main__` blocks in agent files may call `load_dotenv()` and set up loggers (they are entrypoints, not libraries)
