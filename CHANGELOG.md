# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
