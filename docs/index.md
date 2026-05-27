# orchestrator-core Documentation

Welcome to the orchestrator-core documentation. This guide helps you understand, use, and contribute to the orchestrator-core runtime.

## Quick Links

- [README](../README.md) - Installation and usage guide
- [Contributing Guidelines](../CONTRIBUTING.md) - How to contribute
- [Code of Conduct](../CODE_OF_CONDUCT.md) - Community standards

## Architecture

orchestrator-core is a target-agnostic orchestration runtime for multi-stage software engineering workflows.

### Pipeline Stages

1. **Scout** - Scans repositories to discover optimization opportunities and security issues
   - Analyzes repository metadata
   - Performs source code scanning
   - Generates findings report

2. **Architect** - Creates detailed implementation plans
   - Ingests Scout findings via typed contracts
   - Designs safe, executable plans
   - Prioritizes changes by risk and impact

3. **Executor** - Applies code modifications
   - Routes tasks between LLM tiers based on complexity
   - Uses Gemini for simple changes
   - Uses Claude for complex/critical changes
   - Uses Groq for validation and lightweight tasks

4. **Validator** - Validates results
   - Checks syntax correctness
   - Performs type validation
   - Verifies code quality

### Decision Records

Design and architecture decisions are documented in the Architecture Decision Records (ADRs):

- [ADR-0001: Architect Comparison](./adr/0001-architect-comparison.md)
- [ADR-0002: Runtime Boundaries](./adr/002-runtime-boundaries.md)

## Getting Started

### Installation

```bash
git clone https://github.com/Argenis1412/orchestrator-core.git
cd orchestrator-core
pip install -e .
```

### Configuration

Create a `.env` file with your API keys:

```bash
cp .env.example .env
# Edit .env and add your API keys
```

### First Run

```bash
orchestrator run /path/to/project
```

## Development

### Setup Development Environment

```bash
# Clone and navigate
git clone https://github.com/Argenis1412/orchestrator-core.git
cd orchestrator-core

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -v
```

### Running Tests

```bash
# Run all tests
pytest -v

# Run specific test file
pytest tests/test_scout.py -v

# Run with coverage
pytest --cov=src/orchestrator tests/
```

### Code Quality

```bash
# Lint code
ruff check src/

# Format code
ruff format src/

# Type checking (if configured)
mypy src/
```

## Contributing

Please read [CONTRIBUTING.md](../CONTRIBUTING.md) for details on:
- Development setup
- Branch naming conventions
- Commit message format
- Pull request process
- Testing requirements

## Support

- Open an issue for bug reports
- Start a discussion for feature requests
- Check existing documentation first

## License

This project is licensed under the MIT License - see [LICENSE](../LICENSE) file for details.
