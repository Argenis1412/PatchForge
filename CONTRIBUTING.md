# Contributing to Agent Lab

Thank you for your interest in contributing to Agent Lab! This document outlines our contribution guidelines and workflow.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork locally**:
   ```bash
   git clone https://github.com/your-username/agents.git
   cd agents
   ```

3. **Set up development environment**:
   ```bash
   # Using uv (recommended)
   uv sync
   
   # Or using pip
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e ".[dev]"
   ```

4. **Create a `.env` file** based on `.env.example`:
   ```bash
   cp .env.example .env
   # Fill in your API keys
   ```

## Development Workflow

### Branch Naming
Use the following naming convention for branches:
- `feature/description` - for new features
- `fix/description` - for bug fixes
- `docs/description` - for documentation updates
- `test/description` - for test additions

Example: `feature/enhanced-scout-analysis`

### Making Changes

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature
   ```

2. **Write your code** following the project's style (see below)

3. **Run tests**:
   ```bash
   pytest -v
   ```

4. **Lint and format** (optional but recommended):
   ```bash
   ruff check src/
   ruff format src/
   ```

5. **Commit with clear messages**:
   ```bash
   git commit -m "feat: brief description of changes"
   ```

### Commit Message Format
Use conventional commits:
- `feat:` - new feature
- `fix:` - bug fix
- `docs:` - documentation changes
- `test:` - adding or updating tests
- `refactor:` - code refactoring
- `perf:` - performance improvements

Example: `feat: add support for custom analyzer plugins`

### Pull Request Process

1. **Push your branch** to your fork:
   ```bash
   git push origin feature/your-feature
   ```

2. **Create a Pull Request** on GitHub with:
   - Clear title and description
   - Reference to related issues (e.g., "Closes #123")
   - List of changes made

3. **PR Checklist**:
   - [ ] Tests pass locally (`pytest -v`)
   - [ ] Code is linted (`ruff check src/`)
   - [ ] Commits are squashed and messages are clear
   - [ ] Documentation is updated (if applicable)
   - [ ] No merge conflicts with `main`

4. **Wait for review** and address feedback

## Code Style

We use `ruff` for linting and formatting:

```bash
ruff check src/
ruff format src/
```

### Guidelines
- Use clear, descriptive variable names
- Add docstrings to functions and classes
- Keep functions focused and testable
- Follow PEP 8 style guide

## Testing

- Write tests for new features
- Ensure all tests pass: `pytest -v`
- Aim for reasonable test coverage
- Use descriptive test names: `test_scout_identifies_python_files`

Test file location: `tests/test_*.py`

## Documentation

- Update `README.md` for user-facing changes
- Add docstrings to public functions
- Update `docs/` for significant architectural changes
- Reference ADRs in `docs/adr/` for major decisions

## Reporting Issues

When reporting bugs, please include:
- Minimal reproduction steps
- Expected vs. actual behavior
- Environment info (Python version, OS, dependencies)
- Error logs or stack traces

## Questions?

- Check existing issues and discussions
- Review architecture decisions in `docs/adr/`
- Reach out via issue discussion

Thank you for contributing! 🚀
