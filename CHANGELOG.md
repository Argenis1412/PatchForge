# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- **Renamed project**: `agent-lab` → `orchestrator-core`, package import `agent_lab` → `orchestrator`
- **Renamed CLI**: `agent-lab` → `orchestrator`
- Full rebrand of documentation and metadata

### Added
- Multi-agent pipeline for code analysis (Scout, Architect, Executor, Validator)
- Support for multiple LLM providers (Google Gemini, Anthropic Claude, Groq)
- Dry-run mode to preview changes without applying them
- Resume from specific pipeline stages
- Comprehensive logging and artifact tracking

### Fixed
- Improved error handling in executor stage

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
