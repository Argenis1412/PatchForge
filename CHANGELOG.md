# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.1.0] - 2026-07-12

### Highlights

- Docker containerization (`Dockerfile`, `docker-entrypoint.sh`, `.dockerignore`)
- CI/CD reusable GitHub Actions workflow (`patchforge ci` command)
- Asymmetric risk gates (`auto_apply_eligible` field on `RunMetadata`)
- Executor DAG scheduler — task dependency resolution via Kahn's algorithm
- WAL atomic writes for all apply.json persistence
- Circuit breaker externalized to SQLite with thread-safe locking
- GitHub Client, Work Queue schema, Artifact Store, Worker Loop (async infra)
- Executor new-file creation support (Issue #210)
- Executor lifecycle observability: `log_event()` trail across pipeline
- Provider fallback chain (Gemini → OpenRouter → Claude) for architect, scout, validator
- `ci --force-provider`, `plan --force-provider` for provider forcing
- Architect structural context annotations (D-005) — injects Python AST symbols into prompt
- Executor pre-diff `ast.parse()` syntax validation (D-006) — rejects non-Python LLM output
- Centralized `PROJECT_ROOT` in `orchestrator/paths.py`
- Targeted git staging in CI (replaces `git add -A`)
- Makefile for quick QA (`make qa`)
- Claude as third fallback in validator summarizer
- Dogfooding experiments 005–008 (E2E validation, D-001/004/005/006 fixes)

### Fixed

- 🔴 **Validation workspace**: `git add .` + `git commit` now run after `git init` so patches apply correctly to the isolated copy
- 🔴 **Path traversal (Windows)**: `_has_parent_segment` normalizes `\\` → `/` before checking for parent segments
- 🔴 **`normalize_git_url`**: lowercasing only on Windows (preserves case-sensitive Linux paths)
- 🔴 **SSH URL parsing**: 3 explicit regex matchers (ssh-scheme, SCP, HTTP) instead of fragile paired sub — fixes port-number URLs
- 🟠 **Subprocess timeouts**: `timeout=30` added to all 12 `subprocess.run` git calls
- 🟠 **Experiment verification**: shared `verify_experiment_or_warn()` extracted to `experiment.py` — replaces duplicate try/except blocks in `preview.py` and `main.py`
- 🟡 **`Verdict.generated_at`**: `datetime.utcnow` → `datetime.now(timezone.utc)` (Python 3.12 compat)
- 🟡 **`current_head`/`current_branch`**: raise `RuntimeError` instead of returning `""` silently; scanner handles with `try/except`
- 🟡 **`get_current_head` docstring**: corrected to match actual behavior (raises `RuntimeError`)
- 🟡 **`repository_identity`**: added missing `timeout=30`
- **CB thread-safety**: `threading.Lock` in `SqliteCircuitBreakerStore`, `_registry_lock`, `_init_lock` (Issue #219)
- **CRLF corruption**: `newline=""` on all write paths for Windows compat (Issue #192/#193)
- **Phantom paths**: `validate_plan_paths()` module + `[TARGET FILES]` injection into architect prompt (D-001)
- **Workspace hash inconsistency**: normalized path casing on Windows (Issue #149/#150)
- **Validator timeout**: configurable timeout, per-tool spinner, short-circuit on timeout (Issue #151)
- **Staging cleanup**: empty `patch.diff` guard + staging cleanup before re-execution (Issue #159/#160)
- **Silent-failure hardening**: `executor_had_errors` field, `validation_failed` on hard errors (Issue #194/#195)
- **Validator PATH resolution (Windows, no `.venv`)**: `run_ruff()`/`run_pytest()` default commands changed to `sys.executable -m ...`, fixing `VALIDATION_FAILED` false negatives when ruff/pytest are not on the system `PATH` (Issue #223)

### Changed

- `CircuitBreakerStore` externalized to SQLite (Issue #126)
- `write_verdict()` moved from `schemas/experiment.py` to `WorkspaceManager` (Exp 002)
- `DEFAULT_TIMEOUT` 120→300→450s (D-002/D-004 remediation)
- `git add -A` in CI replaced with targeted staging via `parse_diff_files()` (Issue #212)
- Branch naming: `patchforge/{run_id}[/issue_{issue_number}]` (Issue #142)
- Provider chain: Groq → OpenRouter (Issue #162)

## [1.0.0] - 2026-06-08

### Highlights

- Deterministic scan workflow
- Risk gates for plan and patch size
- Failure-state handling and apply rollback
- AI-assisted planning (Claude Sonnet 4.6)
- Preview validation in isolated copy
- Safe git-based apply workflow
- CLI rename from `orchestrator` to `patchforge`
- Legacy `run` command deprecated, hidden from help
- All lint violations resolved (`ruff check .` → 0 errors)

## [0.1.0] - 2026-05-27

### Added
- Initial release as Agent Lab
- `agent-lab run` command for full pipeline execution
- `agent-lab scan` command for reconnaissance only
- Support for custom `.env` file configuration
- CLI interface powered by Typer
- Rich terminal output formatting
- Parallel test execution framework
- Pydantic schemas for type safety
- Comprehensive documentation in docs/

### Planned Features
- Plugin system for custom analyzers
- Performance metrics and benchmarking
- Web UI for pipeline visualization
- Support for additional programming languages
