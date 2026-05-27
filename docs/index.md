# Agent Lab Documentation

Welcome to the Agent Lab documentation. This guide helps you understand, use, and contribute to the Agent Lab framework.

## Quick Links

- [README](../README.md) - Installation and usage guide
- [Contributing Guidelines](../CONTRIBUTING.md) - How to contribute
- [Code of Conduct](../CODE_OF_CONDUCT.md) - Community standards

## Architecture

Agent Lab is a multi-agent pipeline for code analysis and refactoring. Understanding the architecture helps you make the most of the framework.

### Pipeline Stages

1. **Scout 🕵️** - Scans repositories to discover optimization opportunities and security issues
   - Analyzes repository metadata
   - Performs source code scanning
   - Generates findings report

2. **Architect 🧠** - Creates detailed implementation plans
   - Ingests Scout findings
   - Designs safe, executable plans
   - Prioritizes changes by risk and impact

3. **Executor ⚙️** - Applies code modifications
   - Routes tasks between LLM tiers based on complexity
   - Uses Gemini for simple changes
   - Uses Claude for complex/critical changes
   - Uses Groq for validation and lightweight tasks

4. **Validator 🛡️** - Validates results
   - Checks syntax correctness
   - Performs type validation
   - Verifies code quality

### Decision Records

Design and architecture decisions are documented in the Architecture Decision Records (ADRs):

- [ADR-0001: Architect Comparison](./adr/0001-architect-comparison.md)
- [ADR-0002: Runtime Boundaries](./adr/002-runtime-boundaries.md)

### Project Plans

- [Agent Lab Plan](./Agent_lab_plan.md) - Overall project roadmap
- [Fix Execution Plan](./Fix_Execution_Plan.md) - Current fixes and improvements
- [Quality Gate](./QUALITY_GATE.md) - Quality standards and checks

## Getting Started

### Installation

```bash
git clone https://github.com/Argenis1412/agents.git
cd agents
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
agent-lab run /path/to/project
```

## Development

### Setup Development Environment

```bash
# Clone and navigate
git clone https://github.com/Argenis1412/agents.git
cd agents

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
pytest --cov=src/agent_lab tests/
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
