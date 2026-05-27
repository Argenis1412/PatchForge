# Agent Lab v2.0

[![CI Status](https://github.com/Argenis1412/agents/actions/workflows/ci.yml/badge.svg)](https://github.com/Argenis1412/agents/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

Agent Lab is a multi-agent AI framework designed to automatically scan, analyze, and safely refactor or augment codebases. It operates as an autonomous pipeline that systematically discovers optimization opportunities, formulates secure implementation plans, and executes code generation and validation steps.

## Quick Links

- 📖 [Documentation](./docs/index.md)
- 🤝 [Contributing Guidelines](./CONTRIBUTING.md)
- 📋 [Changelog](./CHANGELOG.md)
- 📌 [Architecture Decisions](./docs/adr/)

## Installation

Agent Lab is now a standalone Python library. We recommend installing it within an isolated environment (such as with `uv` or `venv`):

```bash
# Clone the repository
git clone https://github.com/Argenis1412/agents.git
cd agents

# Install the library globally or in your current virtual environment
pip install -e .
```

If you are using `uv`, you can install it seamlessly:

```bash
uv pip install -e .
```

## Environment Setup

Create a `.env` file based on `.env.example`:

```bash
cp .env.example .env
# Edit .env and fill in your API keys
```

Your `.env` file should contain:

```env
GEMINI_API_KEY=your_gemini_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
GROQ_API_KEY=your_groq_key_here
```

## Usage

Once installed, the framework provides a convenient `agent-lab` CLI powered by Typer and Rich.

### 1. Full Pipeline Execution

To run the complete pipeline on a specific target directory:

```bash
# In any directory on your machine, you can run the pipeline pointing to a target project
agent-lab run /path/to/your/project
```

**Options:**
- `--dry-run`: Run the pipeline until the Executor stage, but don't apply any changes.
- `--from-stage`: Resume execution from a specific stage (`scout`, `architect`, `executor`).
- `--env-file`: Specify a custom `.env` file containing API keys.
- `--workspace`: Manually define a workspace path for saving logs and outputs.

### 2. Reconnaissance (Scout) Only

If you only want to analyze a project without planning or modifying code:

```bash
agent-lab scan /path/to/your/project
```

## Architecture

- 🕵️ **Scout**: Scans the repository metadata and source code, discovering optimization opportunities and security issues.
- 🧠 **Architect**: Ingests findings and crafts a detailed, safe implementation plan.
- ⚙️ **Executor**: Follows the implementation plan to apply code modifications using an intelligent routing strategy between different LLM tiers (Gemini, Groq, Claude) based on risk level.
- 🛡️ **Validator**: Checks the resulting codebase for syntax or type errors before declaring the pipeline a success.

For detailed architecture information, see [docs/index.md](./docs/index.md)

## Development

### Setup Development Environment

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -v

# Lint and format
ruff check src/
ruff format src/
```

### Running Tests

```bash
# Run all tests
pytest -v

# Run specific test suite
pytest tests/test_scout.py -v
```

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](./CONTRIBUTING.md) for:
- Development setup
- Branch naming conventions
- Commit message format
- Pull request process

## Code of Conduct

This project adheres to a [Code of Conduct](./CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## License

This project is licensed under the MIT License - see the [LICENSE](./LICENSE) file for details.

## Support

- 📖 Check the [documentation](./docs/index.md)
- 🐛 Open an [issue](https://github.com/Argenis1412/agents/issues) for bug reports
- 💬 Start a [discussion](https://github.com/Argenis1412/agents/discussions) for feature requests
