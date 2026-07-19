# CI/CD Setup Guide

PatchForge ships a reusable GitHub Actions workflow that listens for labeled issues, runs the full pipeline (`scan → plan → preview → apply`) inside a Docker container, and opens a Pull Request with the result.

## Quick Start

Create `.github/workflows/patchforge.yml` in your repository:

```yaml
name: PatchForge
on:
  issues:
    types: [labeled]

concurrency:
  group: patchforge-pipeline-${{ github.event.issue.number }}
  cancel-in-progress: true

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  pipeline:
    if: >-
      github.event.label.name == 'patchforge/process'
      && github.event.issue.pull_request == null
      && !github.event.repository.fork
    uses: Argenis1412/PatchForge/.github/workflows/patchforge-pipeline.yml@main
    with:
      issue-number: ${{ github.event.issue.number }}
      issue-title: ${{ github.event.issue.title }}
      issue-body: ${{ github.event.issue.body }}
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
      OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
```

Then add the `patchforge/process` label to an issue — the pipeline will run automatically.

## Required Secrets

At least one LLM API key must be configured:

| Secret | Provider | Notes |
|--------|----------|-------|
| `ANTHROPIC_API_KEY` | Claude | Recommended for high-quality output |
| `GOOGLE_API_KEY` | Gemini | Free tier available |
| `OPENROUTER_API_KEY` | OpenRouter | Multi-model routing |

Set these in your repository's **Settings → Secrets and variables → Actions**.

## Required Permissions

The caller workflow **must** declare these permissions:

```yaml
permissions:
  contents: write       # push branches
  pull-requests: write  # create PRs
  issues: write         # comment and label issues
```

`workflow_call` permissions are inherited from the caller, not the reusable workflow.

## Workflow Inputs

| Input | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `issue-number` | number | Yes | — | GitHub issue number |
| `issue-title` | string | Yes | — | Issue title |
| `issue-body` | string | Yes | — | Issue body (markdown) |
| `risk-budget` | string | No | `"low"` | Risk budget: `low` or `medium` |
| `base-branch` | string | No | `"main"` | Branch to check out and target for PRs |
| `patchforge-image` | string | No | `ghcr.io/argenis1412/patchforge:latest` | Docker image to use |

## Label Lifecycle

```
Issue created
  │
  ├─ Add label: patchforge/process
  │   → Pipeline starts
  │
  ├─ On success:
  │   → Remove: patchforge/process
  │   → Add: patchforge/completed
  │   → PR created, comment posted
  │
  └─ On failure:
      → Remove: patchforge/process
      → Add: patchforge/failed
      → Error comment posted
```

## Architecture: Container vs. Runner

The pipeline separates concerns between the Docker container and the GitHub Actions runner:

**Inside the container** (no GitHub access):
- Runs `patchforge ci` — the full scan → plan → preview → apply pipeline
- Writes `ci_result.json` to the workspace volume
- Creates a local git branch and commit (no push)

**On the runner** (has `GITHUB_TOKEN`):
- Pushes the branch to the remote
- Creates the Pull Request via `gh`
- Comments on and labels the issue
- Uploads run artifacts

This separation means the container never needs `GITHUB_TOKEN` or the `gh` CLI.

## `patchforge ci` CLI Reference

```
patchforge ci <path> --workspace <dir> [options]
```

| Option | Description |
|--------|-------------|
| `--workspace` | Workspace directory (must be outside target repo) |
| `--issue-file` | Markdown issue file with YAML frontmatter |
| `--issue-number` | GitHub issue number for traceability |
| `--risk-budget` | `low` (default) or `medium` |
| `--allow-dirty` | Allow application on a dirty working tree |
| `--result-file` | Path to write `ci_result.json` (default: `<workspace>/ci_result.json`) |

Exit code 0 on success, 1 on any failure. The result file is always written regardless of exit code.

**Note on `--allow-dirty`:** pre-existing working-tree changes are captured under a private git ref (`refs/patchforge/dirt/<run_id>`) until they're restored. Avoid `git push --mirror` or other wildcard-refspec pushes on the target repo while any such refs are outstanding (e.g. after a crashed run) — those push all local refs, including this one, to the remote.

## Container Paths

When running inside Docker, `run.json` contains container-internal paths (`/repo`, `/workspace`). Use `ci_result.json` for machine-readable output — it contains only portable fields (run ID, branch name, status, affected files with repo-relative paths).

## Re-run Behavior

Each pipeline run generates a unique branch name (`patchforge/run_YYYYMMDD_HHMMSS_<hex>`). Re-triggering the same issue creates a new branch and PR, avoiding collisions.

## Troubleshooting

**Pipeline fails at scan stage**: The target repository must be a valid Git repo with at least one commit. Check that the `base-branch` input matches an existing branch.

**"Workspace is inside target repo" error**: The workspace must be mounted at a path outside the repository. The workflow handles this by mounting to `/tmp/pf-workspace`.

**UID mismatch errors on git push**: The runner step `git config --global --add safe.directory` resolves ownership mismatches between the container UID (1000) and the runner UID (1001).

**No LLM API key error**: Set at least one of `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, or `OPENROUTER_API_KEY` in repository secrets.
