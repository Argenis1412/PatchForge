# Contributing to PatchForge

Thank you for your interest in contributing to PatchForge! This document outlines our contribution guidelines and workflow.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork locally**:
   ```bash
   git clone https://github.com/your-username/PatchForge.git
   cd PatchForge
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

PatchForge follows a structured flow for every change, from issue to merged PR:

```
issue → clarify → criteria → challenge → plan → adversarial review → approval
→ implement → diff review → tests → QA → commit → push
```

Core rules:
- The unit of work is a self-contained issue with limited scope and verifiable acceptance criteria — not a loose refactor.
- Implement only what the issue requires. No unrelated refactors, no speculative improvements.
- Keep diffs minimal and stop to ask if anything is ambiguous.

See [docs/context/Workflow.md](./docs/context/Workflow.md) for the full process, including acceptance criteria format and planning rules.

### AI-Assisted Review Roles

As part of the maintainer's internal process, changes typically pass through four review roles before and after implementation: **Issue Clarifier**, **AC Challenger**, **Adversarial Reviewer**, and **Diff Reviewer**. This is the process the maintainer runs locally to compensate for working without a second human reviewer — it is not an automated check that runs on external pull requests, and you are not required to reproduce it yourself. See [docs/context/Workflow.md](./docs/context/Workflow.md#ai-roles) for details.

### Branch Naming

Use the following naming convention for branches:

```
<type>/issue-<number>-<slug>
```

Allowed types: `feat`, `fix`, `docs`, `refactor`, `chore`

Examples:
```
feat/issue-45-add-user-cache
fix/issue-31-handle-empty-response
docs/issue-33-doctor-docstrings
```

### Making Changes

1. **Create a branch**:
   ```bash
   git checkout -b <type>/issue-<number>-<slug>
   ```

2. **Write your code** following the project's style (see below)

3. **Run QA before committing** (all three are mandatory, not optional):
   ```bash
   ruff check .
   ruff format --check .
   pytest
   ```
   Do not commit if any of these fail.

4. **Commit with a conventional message**:
   ```bash
   git commit -m "<type>(<scope>): <message>"
   ```
   One logical change per commit. Do not add `Co-Authored-By` or AI attribution lines — the human author is the sole author of record.

### Commit Message Format

Use conventional commits:
- `feat:` - new feature
- `fix:` - bug fix
- `docs:` - documentation changes
- `test:` - adding or updating tests
- `refactor:` - code refactoring
- `chore:` - maintenance tasks

Example: `feat(scout): add support for custom analyzer plugins`

### Pull Request Process

1. **Push your branch** to your fork:
   ```bash
   git push origin <type>/issue-<number>-<slug>
   ```

2. **Create a Pull Request** on GitHub with:
   - Clear title and description
   - Reference to the related issue (e.g., "Closes #123")
   - List of changes made

3. **PR Checklist** (must pass before opening the PR, not after):
   - [ ] `ruff check .` passes
   - [ ] `ruff format --check .` passes
   - [ ] `pytest` passes
   - [ ] Documentation is updated (if applicable)
   - [ ] No merge conflicts with `main`

4. **Wait for review** and address feedback

## Code Style

We use `ruff` for linting and formatting:

```bash
ruff check .
ruff format .
```

### Guidelines
- Use clear, descriptive variable names
- Add docstrings to functions and classes where the *why* isn't obvious from the name
- Keep functions focused and testable
- Follow PEP 8 style guide
- Preserve existing style and architecture; reuse existing patterns when possible

## Testing

| Change type         | Tests required?                           |
|----------------------|--------------------------------------------|
| Behavioral change    | Yes                                        |
| Bug fix              | Yes — regression test required             |
| New feature          | Yes                                        |
| Documentation only   | No, unless explicitly requested            |
| Pure refactor        | No — existing tests must continue to pass  |

- Ensure all tests pass: `pytest -v`
- Use descriptive test names: `test_scout_identifies_python_files`

Test file location: `tests/test_*.py`

## Documentation

- Update `README.md` for user-facing changes
- Add docstrings to public functions
- Update `docs/` for significant architectural changes
- Reference ADRs in `docs/adr/` for major decisions
- If you discover technical debt outside the scope of your issue, document it in `docs/context/discoveries.md` instead of fixing it inline

## Reporting Issues

When reporting bugs, please include:
- Minimal reproduction steps
- Expected vs. actual behavior
- Environment info (Python version, OS, dependencies)
- Error logs or stack traces

For security vulnerabilities, see [SECURITY.md](./SECURITY.md) instead of opening a public issue.

## Questions?

- Check existing issues and discussions
- Review architecture decisions in `docs/adr/`
- Reach out via issue discussion

Thank you for contributing! 🚀
