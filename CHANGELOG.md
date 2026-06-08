# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
