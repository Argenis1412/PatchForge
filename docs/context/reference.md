# PatchForge — Reference

> Historical context, technical debt, and design discussions.
> Not required reading for every session. Consult when investigating specific topics.
>
> For daily session context, see `docs/context/CONTEXT.md`.
> For implementation discoveries, see `docs/context/discoveries.md`.

---

## Known Technical Debt

### Pre-existing (unrelated to V1 issues)

| Item | File(s) | Severity | Notes |
|------|---------|----------|-------|
| `main.py` god object (551 lines) | `main.py` | High | CLI + business logic mixed. Bigger than `pipeline.py`. |
| `_extract_json()` fragility | `agents/architect.py` | High | 52-line manual JSON parser. Single point of failure if LLM format changes. |
| Docstring coverage <80% | All | Medium | CodeRabbit flagged post-#9. Not a blocker for V1. |

### V1-Specific

| Item | Details | Why Deferred |
|------|---------|--------------|
| `ScanFindings.warnings` not implemented | TypeScript warnings mixed into `support_reasons` | Functional but semantically impure. Low priority. |
| Workspace path mismatch | `scan` writes to `~/.cache/patchforge/` by default, other commands use `--workspace` | Would require unify CLI options across 3 commands. |
| `force_reset_apply` not reusable ✓ | Resolved in T-02 (#81): `rollback_to_commit()` in executor.py raises `RollbackError`. | `main.py` no longer calls `force_reset_apply` directly. |
| `pre_apply_head` capture in caller | `main.py:apply`; Low | `rollback_to_commit(repo_root, target_sha)` requires caller to provide a valid SHA. If apply moves to executor.py in the future, HEAD capture must move too. |
| Architect not budget-aware | Generates ideal plan; gate filters post-hoc | $0.04 spent on plans blocked at `risk_budget=low`. Open design question. |
| Self-audit limited by environment | V1 `scan` succeeded but V1 not supported (ruff/pytest missing from PATH) | Environment issue, not product gap. |

---

## Failed Approaches

| Date | Approach | Problem |
|------|----------|---------|
| Jun 6 | Issue #9 v1.0: `LifecycleManager` class + `check_patch_compatibility(patch_path, base_commit)` | Rejected — class inconsistent with codebase (pure function pattern). Function mixed git + domain concerns. |
| Jun 6 | Issue #9 v1.0: `STALE` left undefined | Not implementable without deterministic condition. |
| Jun 7 | Issue #49: `Task(frozen=True)` + `__hash__` | Pydantic frozen prevents attribute mutation needed for post-hoc `status="blocked"` assignment. Replaced with mutable model + explicit `model_dump()` → `Task(**dict)` reconstruction during dev, then simplified to direct attribute assignment after removing frozen. |

---

## Open Design Questions

1. **Budget-aware Architect.** Should Architect receive `max_files` and `risk_budget` in its prompt and generate a constrained plan? Currently generates ideal plan; gate filters post-hoc. Trade-off: constrained plans at low budget save $0.04 per run; ideal plans are better for audit.

2. **`ScanFindings.warnings` field.** Current approach (TypeScript warnings in `support_reasons`) is functional but semantically impure. Cost to add is low but requires schema update, serialization, tests.

3. **Workspace path unification.** `scan` writes to `~/.cache/patchforge/workspaces/` by default. Other commands may use `--workspace`. A unified path would let commands resume V1 findings. Currently blocked by CLI design.

---

## Self-Pipeline QA History

### Jun 8, 2026 (Post-#59)

Periodic audit after merging Issue #59 (lint cleanup).

**Run ID:** `run_20260608_173439_3a96c3`
**Files scanned:** 62 | **Hotspots:** 37
**Result:** All metrics unchanged from previous audit. No regressions detected.

### Jun 8, 2026 (Post-#53)

Pipeline audit after `run` command deprecation. V1 deterministic scanner in use.

**Run ID:** `run_20260608_010507_8771dc`
**Files scanned:** 62 | **Hotspots:** 37
**Result:** V1 scanner provides perfect coverage of V1 subsystem (6/6 files) at zero cost, compensating for Scout's blind spots.

---

## Known Working Directory Issue

The configured working directory was `orchestrator-core/` but the actual project root is `PatchForge/`. Always use `workdir` parameter or absolute paths in bash commands. *(Kept for historical reference — resolved in later sessions.)*

---

## Sprint Closeout — V1.0.0

**16/16 V1 issues implemented.** CLI renamed from `orchestrator` to `patchforge`.

| Layer | Name | Notes |
|-------|------|-------|
| CLI (primary) | `patchforge` | All help text, warnings, docs |
| CLI (alias) | `orchestrator` | Entry point alias, removed in V2 |
| Python package | `orchestrator` | `import orchestrator`, `src/orchestrator/` |
| Repository | `PatchForge` | GitHub name, external branding |
| Config file | `orchestrator.json` | Per-project config (unchanged) |

### Phase 2 Blockers (pre-prioritized)

| ID | Blocker | Target | Risk |
|----|---------|--------|------|
| T-03 | Hardened JSON parsing | `architect.py`, `risk.py` | Brittle `_extract_json()` multiplies risk with new agents |
| T-05 | Enum-based risk levels | `risk.py` | Stringly-typed risk levels across schemas |
| T-07 | Exception hierarchy + circuit breaker | `agents/*.py` | No typed failure isolation for providers |
| T-01 | Path traversal hardening | `executor.py`, `workspace.py` | No path construction contract enforcement |
| T-06 | Risk gate policy config | `risk.py` | Thresholds are hardcoded, not configurable |
| — | Integration test suite | `pipeline/*` | No baseline E2E tests for refactor safety |
| — | LLM provider circuit breaker | `clients/*.py` | No graceful degradation for provider failures |
