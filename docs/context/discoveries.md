# Technical Debt Discoveries

> Log of technical debt discovered during issue implementation that was outside the issue scope.
> Entries are added by the Diff Reviewer step during implementation (step 11).
> Periodically reviewed and promoted to `reference.md` (Known Technical Debt) during maintenance.

## Entry Format

```markdown
### [YYYY-MM-DD] Issue #N — Title

- **File:** `path/to/file.py:123`
- **Debt:** Concise description of the problem
- **Discovered by:** Diff Reviewer / Implementation
- **Why deferred:** Not part of issue scope (non-goal)
```

---

## Log

### ✅ [2026-07-15] Issue #232 — Audit bundle manifest mirrors sensitive `RunMetadata` fields unredacted (RESOLVED in #234)

- **File:** `src/orchestrator/commands/export_audit.py` (manifest construction), `src/orchestrator/schemas/audit_manifest.py`
- **Debt:** `AuditManifest.run_metadata` embeds the full, unredacted `RunMetadata.model_dump(mode="json")` — including `secrets_ref`, `env_file`, `workspace_path`, `staging_dir`, and `logs_dir`. `export-audit` is explicitly meant to produce a deliverable handed to third-party auditors; these fields leak internal filesystem layout and a reference to where secrets live (not the secret value itself, but still an internal-topology disclosure).
- **Discovered by:** Implementation (accepted deliberately during planning, not caught by review)
- **Resolution:** Issue #234 added an opt-in `--redact` flag that replaces `secrets_ref`, `env_file`, `workspace_path`, `target_path`, `staging_dir`, `logs_dir`, and `provider_config` with a `"[REDACTED]"` sentinel (only when the field is set) in both `manifest.json`'s `run_metadata` and the raw `artifacts/run.json` file — an adversarial review during planning caught that redacting only the manifest would leave the same data sitting unredacted in `run.json`. Default (no flag) behavior is unchanged, preserving the structural-mirror mandate for composition with Approval Provenance (#5). An anti-rot test (`test_redact_fields_cover_all_run_metadata_fields`) fails CI if a future field is added to `RunMetadata` without being classified as redact-worthy or public.

### ✅ [2026-07-15] Issue #232 — `export-audit` has no lock between the terminal-state check and archiving (RESOLVED in #235)

- **File:** `src/orchestrator/commands/export_audit.py` (`export_audit()`)
- **Debt:** The run's `status` is checked once, then the run directory is walked and hashed in a separate pass. No repo/run lock is held in between. A concurrent process rewriting `run.json` or an artifact after the status check but before archiving could produce a bundle whose manifest reflects a moment that never fully existed on disk (mismatched status vs. content). Each individual artifact read is now a consistent single-read snapshot (fixed in the same PR), but the run as a whole is not locked across the full export.
- **Discovered by:** Implementation, accepted per roadmap Cuts ("no repo lock is acquired — Invariant #3 already guarantees per-artifact atomicity via WAL")
- **Resolution:** Issue #235 added opt-in `worker_id`/`coordination_db_dir` params to `export_audit()`, mirroring `apply.py`'s pattern. When `coordination_db_dir` is provided, a repo lock is held across the status check, file walk, and hash loop (the full read window). Lock failure aborts with exit code 8. Supersedes the original non-goal in `docs/planning/p4/04-audit-bundle-export.md`. Worker identity uses `uuid.uuid4().hex` (or `{worker_id}:export-audit` suffix) to prevent reentrant-release and silent same-identity bypass. Metadata is refreshed from the locked read of `run.json` to guarantee manifest/tarball consistency. No CLI flags added — infrastructure only, for future `work_queue.py` integration.

### [2026-07-16] Issue #232 — GPG signature verification trusts any locally-known key (tracked in #239)

- **File:** `src/orchestrator/commands/export_audit.py` (`_verify_gpg_signature()`)
- **Debt:** `verify-audit` accepts any cryptographically valid signature from the operator's local GPG keyring — there is no signer-fingerprint allowlist, so a bundle "verified" only proves *some* trusted-by-this-machine key signed it, not that it was PatchForge (or a specific authorized party) that produced it.
- **Discovered by:** Bot review (signer-allowlist finding, evaluated and explicitly out of scope for #232) + implementation review
- **Why deferred:** A signer allowlist is a new authorization feature (config surface, storage format, trust-model design) with no AC in #232 requesting it; documented as a deliberate non-goal in `_verify_gpg_signature`'s docstring. Split out of the original #236 (which bundled it with the CI gap below) during #236 triage on 2026-07-16 — the two problems have independent designs and risk profiles. Tracked as P5/Scout-vision candidate in #239.

### ✅ [2026-07-15] Issue #232 — CI never exercises the real `gpg` binary (RESOLVED in #236)

- **File:** `.github/workflows/ci.yml`, `tests/test_export_audit.py`
- **Debt:** All GPG-path tests (`test_gpg_sign_and_verify` and friends) are `skipif`-guarded on `gpg` being present in `PATH` with a usable secret key; `ubuntu-latest` has `gpg` pre-installed but no secret key, so these tests skipped in CI on every run — only the mocked `subprocess.run` path was exercised, never the real binary's success path.
- **Discovered by:** Bot review + implementation review during #232
- **Resolution:** Issue #236 added a CI step that generates an ephemeral RSA-2048 keypair in an isolated `GNUPGHOME` (via `mktemp -d`, exported through `GITHUB_ENV`) before the test steps run. A post-generation check (`gpg --list-secret-keys | grep -q sec`) fails the step loudly if the key wasn't actually usable, preventing a silent regression back to skip-only behavior. No test or production code changes — the existing two-tier skip logic passes once a usable key exists. Accepted risk: keyring contention under parallel test execution is avoided today because `pytest -n auto --dist loadfile` groups same-file tests onto one xdist worker, not because of an explicit lock; flagged for revisit if CI flakiness appears or the dist strategy changes.

### ✅ [2026-07-10] Dogfooding 008 — `plan` CLI gaps and non-determinism (PARTIALLY RESOLVED)

- **File:** `src/orchestrator/main.py`, `src/orchestrator/commands/plan.py`, `src/orchestrator/agents/architect/`
- **Debt:** `plan` did not accept `--force-provider` (unlike `preview`/`ci`), always using
  the default Architect model. Additionally, `ci --force-provider` only forced the executor,
  not the architect step within ci, creating a silent cost inconsistency.
- **Discovered by:** Dogfooding 008 (`docs/experiments/dogfooding-008.md`)
- **Resolution:** Added `--force-provider` to `plan` and wired it through to the architect
  provider chain. Also fixed `ci` to forward `force_provider` to the architect calls.
  Remaining open items: `--workspace` requirement is still undocumented in `--help`;
  Architect output non-determinism is inherent LLM sampling variance (not fixable here).

### ✅ [2026-07-10] `test_executor_emits_task_skipped` is order-dependent (RESOLVED)

- **File:** `tests/conftest.py`
- **Debt:** Failed when run as part of the full suite but passed in isolation. Root cause:
  the `_reset_circuit_breakers` fixture reset CB state in-memory only (direct attribute
  assignment) but never persisted to SQLite. Subsequent tests' CB `_reload_state()` loaded
  stale OPEN state from the store, overwriting the fixture's reset.
- **Discovered by:** Dogfooding 008 full-suite QA run
- **Resolution:** Replaced direct attribute mutations with `cb.reset()` which does the same
  mutations AND calls `_persist_state()`.

### [2026-07-10] Dogfooding 008 — `plan --workspace` undocumented requirement

- **File:** `src/orchestrator/commands/plan.py`
- **Debt:** `plan` requires an explicit `--workspace` matching `scan`'s computed workspace
  hash or it fails with "Run ... does not exist" — undocumented in `--help`. Separately,
  running `plan` twice with identical inputs produced different outcomes (non-deterministic
  Architect output from LLM sampling variance).
- **Discovered by:** Dogfooding 008 (`docs/experiments/dogfooding-008.md`)
- **Why deferred:** `--workspace` docs are a small, separate CLI issue; non-determinism is
  inherent to LLM sampling and lower priority.

### ✅ [2026-07-10] Issue #214 — `SqliteCircuitBreakerStore` thread-affinity (RESOLVED #219)

- **File:** `src/orchestrator/storage/__init__.py:30` (`_sqlite_connect()`) and `src/orchestrator/storage/lock.py:41` (`SqliteCircuitBreakerStore.__init__`)
- **Debt:** `_sqlite_connect()` calls `sqlite3.connect()` without `check_same_thread=False`. SQLite's default `check_same_thread=True` raises `ProgrammingError` on any access from a thread other than the connection's creator, regardless of any Python-level lock. Consequence: a `CircuitBreaker` backed by the production `SqliteCircuitBreakerStore` (used by `providers.py`, `work_queue.py`) will still crash if `call()` is invoked from a thread other than the one that constructed the store, even after Issue #214's `self._lock` fix. The Python lock serializes access but does not change which OS thread is calling `.execute()`.
- **Discovered by:** Adversarial review during Issue #214 planning
- **Resolution:** Added opt-in `check_same_thread` parameter to `_sqlite_connect()` (default `True` to preserve safety net for other callers). `SqliteCircuitBreakerStore` passes `False` and serializes all connection access with `self._conn_lock`. Regression test: `test_sqlite_store_cross_thread`.

### ✅ [2026-07-10] Issue #214 — `circuit_breaker_for()` registry check-then-set race (RESOLVED #219)

- **File:** `src/orchestrator/circuit_breaker.py:388` (`circuit_breaker_for()`) and `src/orchestrator/providers.py:_init_circuit_breakers()`
- **Debt:** The module-level `_registry[provider_name]` check-then-set in `circuit_breaker_for()` is unguarded. Two threads racing the same never-seen provider can each pass the `if provider_name not in _registry:` check and construct two distinct `CircuitBreaker` objects, each with its **own** `self._lock`. Two different lock objects provide no mutual exclusion against each other, so Issue #214's per-instance lock guarantee does not hold across that window. The same pattern manifests in production at `providers._init_circuit_breakers()`, which uses an unguarded check-then-set on the `_coord_store` / `_cb_gemini` / `_cb_openrouter` / `_cb_claude` module globals.
- **Discovered by:** Adversarial review during Issue #214 planning
- **Resolution:** Added `_registry_lock = threading.Lock()` guarding the check-then-set in `circuit_breaker_for()`. Added `_init_lock` with the same pattern in `providers._init_circuit_breakers()`. Lock ordering documented in `CircuitBreaker` docstring. Regression test: `test_registry_singleton_under_contention`.

### ✅ [2026-06-15] Phase 3 — `run_ruff()` mutates caller `cmd_override` (RESOLVED)

- **File:** `src/orchestrator/agents/validator/runners.py:130`
- **Debt:** `run_ruff()`, `run_pytest()`, and `run_tsc()` assign `cmd = cmd_override`
  without copying, then `run_ruff()` mutates via `cmd.extend()`. The caller's
  original list object is polluted for any subsequent usage.
- **Discovered by:** CodeRabbit during Phase 3 extraction review
- **Resolution:** All 6 `cmd_override` assignments now use `list(cmd_override)`
  to create a defensive copy. Fix branch `fix/cmd-override-mutation`.

### ✅ [2026-06-14] Issue #79 — `write_verdict()` I/O in schemas/ (RESOLVED)

- **File:** `src/orchestrator/schemas/experiment.py`
- **Debt:** `write_verdict()` co-locates file I/O with schema definition.
  The codebase pattern puts I/O in `workspace.py`. Consistent with this
  issue's scope (minimal, no pipeline touch) but inconsistent with the
  established pattern.
- **Discovered by:** Implementation
- **Resolution:** Moved to `WorkspaceManager.write_verdict()` in `workspace.py`
  as part of Experiment 002. `schemas/experiment.py` now contains only the
  pure `Verdict(BaseModel)` schema.

### ✅ [2026-06-14] Experiment 002 — Executor skips dependent tasks when dependency reports "already applied" (RESOLVED)

- **File:** `src/orchestrator/agents/executor.py`
- **Debt:** When a task dependency (e.g. T1 — audit) produces "no changes — already applied",
  the executor skips downstream tasks (e.g. T2 — add to workspace.py) even though
  T2 is not a no-op. The task dependency DAG is flattened into a linear sequence
  and adjacent skip logic poisons the chain.
- **Discovered by:** Experiment 002 dogfooding
- **Resolution:** Issue #98 replaced the flat sequential loop with a DAG scheduler
  (Kahn's topological order) that respects `Task.dependencies`, detects cycles,
  and propagates `SKIPPED` status correctly. The placeholder "no changes — already applied"
  string was replaced by `TaskStatus.NOOP` with `diff=None`.

### ✅ [2026-06-14] Experiment 002 — Groq API 403 (key expired/rate-limited) (RESOLVED)

- **File:** `src/orchestrator/agents/executor.py`
- **Debt:** Groq API key returns 403 Forbidden. All medium-risk tasks route to Groq;
  when Groq is unavailable, the pipeline stalls. No fallback chain exists
  (Groq → Gemini → Claude).
- **Discovered by:** Experiment 002 dogfooding
- **Resolution:** Issue #100 implemented a unified provider fallback chain that
  handles all recoverable provider errors (CB open, 403, rate limits, etc.)
  across all risk levels.

### ✅ [2026-06-14] Experiment 002 — Risk budget defaults too restrictive for multi-file refactors (RESOLVED)

- **File:** `src/orchestrator/commands/scan.py:138-140`
- **Debt:** `risk_budget="low"` and `max_files=2` block refactors of 3+ files.
  A pure refactor (code movement only, no logic change) should not require
  manual `run.json` editing.
- **Discovered by:** Experiment 002 dogfooding
- **Resolution:** Experiment 003 added `--risk-budget` flag to `patchforge scan`
  (`scan.py:150-160`), mapping `low`/`medium`/`high` to `(max_files, max_diff_lines)`.
  Tests in `tests/test_risk_budget.py`.

### [2026-06-11] Issue #77 — Pre-existing ruff formatting violations

- **File:** `scripts/bootstrap_check.py`, `tests/test_run_metadata.py`
- **Debt:** Two files have formatting violations per `ruff format --check .` that were not introduced by this issue. Blocking `ruff format --check .` from passing.
- **Discovered by:** Implementation
- **Why deferred:** Outside issue scope; auto-fix via `ruff format` is needed for pre-commit compliance.

### ✅ [2026-06-11] Issue #77 — RunMetadata.schema_version default duplicado (RESOLVED)

- **File:** `src/orchestrator/schemas/artifacts.py:47`
- **Debt:** `schema_version: int = 1` hardcodes the value instead of using `schema_version: int = CURRENT_SCHEMA_VERSION`. If someone increments the constant but omits the field default, `RunMetadata` would produce artifacts with the wrong version.
- **Discovered by:** AI review bot (CodeRabbit)
- **Resolution:** Field default now uses `CURRENT_SCHEMA_VERSION` directly.

### [2026-06-13] Issue #87 — Circuit Breaker (T-07 Part B)

- **File:** `src/orchestrator/circuit_breaker.py`
- **Debt:** `CircuitBreaker._consecutive_failures` and `_half_open_in_flight` lack thread-safe protection. Consistent with the existing pattern in `clients/*.py` (no locks, GIL-dependent), but if P3 introduces threading or async workers, it will be a race condition.
- **Discovered by:** Adversarial audit during issue design
- **Why deferred:** No-threading is a project invariant in V1. Revisit with P3 (async workers).
- **Resolution [2026-07-10, Issue #214]:** ✅ RESOLVED for in-process instance-state races. A single `threading.Lock` (`self._lock`) now serializes all mutations of `_state`, `_consecutive_failures`, `_last_failure_time`, `_recovery_timeout`, `_half_open_in_flight`, and their persistence via `_persist_state()`. The lock is released while `fn()` executes (never held across a network call). Scope explicitly does NOT cover: (a) cross-process coordination (already an accepted relaxation via `SqliteCircuitBreakerStore`); (b) `SqliteCircuitBreakerStore` thread-affinity (`check_same_thread=True` default — see separate entry below); (c) the `circuit_breaker_for()` registry race (see separate entry below); (d) the inherent `fn()`-masking window (concurrent outcome handlers can mask each other because `fn()` runs unlocked — documented in the class docstring).

- **File:** `src/orchestrator/exceptions.py:101`
- **Debt:** `CircuitBreakerOpenError.state` has type hint `object` instead of `CircuitBreakerState` to avoid a circular import between `circuit_breaker.py` and `exceptions.py`. Does not affect runtime.
- **Discovered by:** Implementation
- **Why deferred:** Breaking the circular import requires moving `CircuitBreakerState` to a third module or having `exceptions.py` import from `circuit_breaker`. Outside the scope of T-07B.

### ✅ [2026-06-11] Issue #71 — Exception hierarchy (T-07 Part A) (RESOLVED)

- **File:** `src/orchestrator/agents/scout.py:145` (stale path: now `scout/provider.py`)
- **Debt:** Bare `raise` in `call_gemini()` except block propagates the original exception type instead of `ProviderError`, making downstream `except PatchForgeError` handlers unable to catch it. The `raise ProviderError("gemini", ...)` on line 147 is dead code — it never executes because the bare `raise` always exits before reaching it.
- **Discovered by:** AI review bot during T-07 Part A implementation
- **Resolution:** Scout was refactored into `scout/provider.py` with a full
  `_call_chain`-based provider chain (`_SCOUT_CHAIN`). The bare-raise and dead
  `ProviderError` code no longer exist. On chain exhaustion, `provider.py:89`
  raises `ProviderError("provider_chain", ...)` cleanly.


### ✅ [2026-06-25] Issue #145 — `force_provider` override not auditable via `log_event` (RESOLVED)

- **File:** `src/orchestrator/agents/executor/__init__.py:69`
- **Debt:** `--force-provider` override is logged only to `executor.log` via `_get_logger().info()`. No `log_event()` is emitted to `pipeline.jsonl` because the executor does not receive `run_id`/`logs_dir` from the pipeline caller. Any future caller (test, API, new command) that passes `force_provider` without manually logging would create an audit hole.
- **Discovered by:** Post-implementation audit
- **Resolution:** [2026-07-09] Issue #208 — `executor_agent.run()` now accepts `logs_dir`/`run_dir` and emits a full lifecycle event trail (`executor_start`, `task_start`, `file_start`/`file_end`, `task_end`, `task_skipped`, `executor_end`) via `log_event()`.

### ✅ [2026-06-15] Phase 4 — Provider clients lack consistent timeout (RESOLVED)

- **File:** `src/orchestrator/clients/gemini_client.py:11`, `anthropic_client.py:11`, `openrouter_client.py:16`
- **Debt:** All three provider clients have inconsistent or missing timeouts:
  - Gemini: `genai.Client()` has no timeout — requests can hang indefinitely.
  - Anthropic: uses SDK default (10 min) instead of `TIMEOUT_SECONDS` (60s).
  - OpenRouter: hardcodes 30s instead of `TIMEOUT_SECONDS` (60s).
  The `TIMEOUT_SECONDS` constant exists in `providers.py` but no client consumes it.
- **Discovered by:** CodeRabbit AI review during Phase 4
- **Resolution:** `TIMEOUT_SECONDS` moved to `clients/__init__.py`. All three clients
  now consume it: Gemini via `HttpOptions(timeout=60000)` (ms), Anthropic via
  constructor `timeout=60`, OpenRouter via `httpx.Client(timeout=60)`.

### ✅ [2026-06-15] Phase 4 — `__init__.py` import binding prevents submodule monkeypatch (RESOLVED)

- **File:** `src/orchestrator/agents/executor/__init__.py` (general pattern)
- **Debt:** When `__init__.py` does `from .applier import _apply_task`, the binding is captured at import time. Monkeypatching `applier._apply_task` does not affect `run()`. The fix was to import the module (`from . import applier as _applier`) and access via `_applier._apply_task()`. This pattern is not documented as a convention, making it easy to reintroduce the bug in future extractions (Phase 5-7).
- **Discovered by:** Phase 4 execution (8 tests failed due to ineffective monkeypatch)
- **Resolution:** Phase 4.5 — `docs/import-convention.md` documents the lazy import pattern inside function bodies, with GOOD/BAD examples and a monkeypatch rationale.

### ✅ [2026-06-15] Phase 4 — Dead `mock_openrouter` fixture in conftest.py (RESOLVED)

- **File:** `tests/conftest.py:30-37`
- **Debt:** The `mock_openrouter` fixture patches `orchestrator.agents.executor.providers._call_openrouter` but no test in the suite uses it. Dead code. Furthermore, even if a test did use it, it would not work — `_PROVIDER_CHAIN` stores references to `_call_openrouter` at import time, so the monkeypatch would have no effect.
- **Discovered by:** Phase 4 dependency audit
- **Resolution:** Removed `mock_openrouter` and `mock_subprocess` dead fixtures from conftest.py.

### ✅ [2026-06-15] Phase 4 — `PROJECT_ROOT` depends on `__file__` — brittle on relocation (RESOLVED)

- **File:** `src/orchestrator/agents/executor/__init__.py:25-27`
- **Debt:** `PROJECT_ROOT` resolves via `Path(__file__).resolve().parent.parent.parent.parent`. This required an extra `.parent` when moving from `executor.py` to `executor/__init__.py`. Every time a module moves within the `agents/` tree, any `__file__`-based path constant silently breaks. Should use `PROJECT_ROOT` from a shared module or always via environment variable.
- **Discovered by:** Phase 4 execution
- **Resolution:** [2026-07-10] Issue #212 — `PROJECT_ROOT` centralized in
  `orchestrator/paths.py` with stable 2-parent resolution. Grep confirmed
  only one live consumer (`executor/__init__.py`); the "unified strategy
  for 4 agents" blocker was obsolete. Executor imports the module (not
  the symbol) per `docs/import-convention.md`.

### ✅ [2026-06-30] Issue #183 — `git add -A` in `ci.py` stages all untracked files (RESOLVED)

- **File:** `src/orchestrator/commands/ci.py`
- **Debt:** `git add -A` in the apply stage stages all untracked files in the repo.
  When `--allow-dirty` is used and the working tree has generated files (e.g.
  `orchestrator.json`, `.pyc` caches), they get committed.
- **Discovered by:** Post-implementation code review
- **Resolution:** [2026-07-10] Issue #212 — Replaced `git add -A` with
  `git add -- <files>` where the file list is derived from the applied patch's
  `diff --git` headers via `parse_diff_files()` (refactored from `risk.py`).
  Only b-side paths are emitted (renames emit destination only; deletes are
  staged via git's implicit detection). Empty-parse guard returns `_apply_fail`
  without rollback to preserve valid working-tree state. Integration tests
  verify untracked files are excluded.

### ✅ [2026-06-30] Issue #183 — `force_provider` override not propagated to CI pipeline agents (RESOLVED)

- **File:** `src/orchestrator/commands/ci.py`
- **Debt:** `patchforge ci` does not expose a `--force-provider` flag. The executor
  and architect agents use their default provider routing. In contrast,
  `patchforge preview` supports `--force-provider` for debugging. Adding it to
  `ci` requires threading the parameter through all agent calls in `execute()`.
- **Discovered by:** Post-implementation code review
- **Resolution:** [2026-07-09] Issue #208 — `patchforge ci` now accepts `--force-provider`, forwards it to the executor, emits a symmetric `force_provider_override` event, and records it on `CiResult.force_provider`.

### [2026-06-30] Issue #183 — `latest` Docker tag non-deterministic in workflow default

- **File:** `.github/workflows/patchforge-pipeline.yml:26`
- **Debt:** The `patchforge-image` input defaults to `ghcr.io/argenis1412/patchforge:latest`.
  The `latest` tag is mutable — a new image push between issue creation and
  pipeline execution could change behavior silently. The original plan specified
  version pinning (`0.1.0`) but the implementation uses `latest` for ease of
  adoption.
- **Discovered by:** Post-implementation code review
- **Why deferred:** External callers can pin via the `patchforge-image` input.
  Version-tagged images require a publishing pipeline (separate issue). Low
  impact while PatchForge is the only consumer.

### ✅ [2026-07-02] Dogfooding 002 — Executor generates CRLF patch on Windows (RESOLVED)

- **File:** `src/orchestrator/agents/executor/` (diff generation path)
- **Debt:** On Windows, the executor writes `patch.diff` with CRLF (`\r\n`) line endings.
  The validation workspace uses `git apply` internally, which expects LF. Result: "error: patch
  does not apply" even when the patch is semantically correct. Confirmed: source file uses LF
  (24 LF, 0 CRLF), patch has 16 CRLF lines. The patch applies correctly after CRLF→LF conversion.
- **Discovered by:** Dogfooding 002
- **Resolution (Issue #192):** Added `newline=""` to all write paths that can produce `patch.diff`:
  - `src/orchestrator/storage/local_store.py:23` — primary path via `LocalArtifactStore.write()`
  - `src/orchestrator/storage/work_queue.py:199` — `_restore_checkpoint` path
  - `src/orchestrator/storage/work_queue.py:228` — `_hydrate_stage` path
  - `src/orchestrator/storage/__init__.py:40` — `_wal_write` (consistency; JSON is not broken by CRLF but matches the pattern)
  Regression test `test_local_store_preserves_lf` verifies raw bytes contain no `\r\n`.
  Idempotency test `test_local_store_lf_idempotency_git_apply` confirms `git apply --check` passes
  with `core.autocrlf=false` pinned.

### ✅ [2026-07-02] Dogfooding 002 — Git root mismatch for subdirectory targets (AUDITED — no bug in PatchForge commands)

- **File:** `src/orchestrator/validation_workspace.py` and `src/orchestrator/commands/apply.py`
- **Debt:** When `target_path` is a subdirectory of the git root (e.g. `Portf-lio/backend/`
  while git root is `Portf-lio/`), the patch uses paths relative to `target_path`
  (`app/schemas/philosophy.py`) but `git diff` reports relative to git root
  (`backend/app/schemas/philosophy.py`). The validation workspace isolates from `target_path`
  so `apply_patch_to_copy` works, but `patchforge apply` using the git root could fail.
  Latent risk if the user runs `git apply` manually from the git root.
- **Discovered by:** Dogfooding 002
- **Audit result (Issue #192 session):** PatchForge's own commands are protected:
  - `apply.py` uses `git -C target_path` throughout (lines 121, 169, 328) — operates relative to `target_path`, not git root.
  - `validation_workspace.py` creates a fresh `git init` at the temp copy root (lines 45-83) — completely isolated from the outer git tree.
  - Diffs are generated relative to `target_path` via `task.files_to_modify[0]`.
  - The mismatch is a risk only if the user manually runs `git apply` from the git root using the generated `patch.diff`. External to PatchForge's automated flow.

### ✅ [2026-07-07] Dogfooding 005 — Default timeout (300s) insufficient for self-dogfooding post-PR-#195 (RESOLVED)

- **File:** `src/orchestrator/agents/validator/runners.py:14`
- **Debt:** D-002 fix (PR #195) raised `DEFAULT_TIMEOUT` from 120s to 300s. PR #195
  also added 14 new test cases (`test_plan_validation.py`, `test_preview_hard_errors.py`,
  `test_validator_timeout.py` additions), growing the PatchForge test suite from 619 to
  633 tests. The combined suite now exceeds 300s when run via the validator in a
  self-dogfooding scenario. The fix raised the floor but the floor moved simultaneously.
- **Discovered by:** Dogfooding 005
- **Resolution (Issue #198, PR #199):** `DEFAULT_TIMEOUT` raised from 300s to 450s.
  Test floor assertion updated to `>= 450`. The `--validator-timeout` hint remains
  available for projects needing custom values.

### ✅ [2026-07-07] D-001 root cause — Architect generates phantom file paths (RESOLVED)

- **File:** `src/orchestrator/agents/architect/file_collector.py` (new), `src/orchestrator/agents/architect/prompts.py`
- **Debt:** The Architect Agent hallucinated file paths because its prompt had no context
  about which files actually exist in the target repo. The post-hoc guard
  `validate_plan_paths()` (PR #195) caught phantom paths but wasted tokens and
  produced artificial blockers.
- **Discovered by:** Dogfooding 004
- **Resolution:** New `file_collector` module injects a `[TARGET FILES]` block into both
  `ARCHITECT_PROMPT` and `ISSUE_ARCHITECT_PROMPT` with the full target file listing
  (all extensions, no filter). Path constraint instruction added. Cap at 500 paths
  with truncation warning. Build artifact dirs excluded via `_EXTRA_IGNORE_DIRS`.
  `validate_plan_paths()` remains as defense in depth.
- **Remaining risks:** alphabetical truncation may bias file selection; `.gitignore`
  not fully parsed (only common artifact dirs excluded); LLM may still ignore the
  constraint (safety net catches this).
- **Dogfooding-006 outcome (2026-07-08):** Fix validated. Zero phantom paths for
  existing files across 7-file plan. 195/195 paths injected (truncated=False) —
  500-path cap non-binding for PatchForge repo. Architect found real files in the
  executor package but targeted `scheduler.py` instead of `__init__.py` — correct
  path, wrong file within the package (see D-005). Alphabetical truncation untested
  (repo < 500 files).

### ✅ [2026-07-08] Dogfooding 006 — D-005: Architect targets wrong file within package (RESOLVED)

- **File:** `src/orchestrator/agents/architect/file_collector.py`
- **Debt:** The `[TARGET FILES]` block lists all files but provides no structural context
  about which file contains what functionality. For packages with multiple submodules
  the architect can pick the correct directory but the wrong file within it. In D006
  it targeted `scheduler.py` (DAG builder) instead of `__init__.py` (task loop with `run()`).
  The executor then wrote LLM tool-call markup into scheduler.py, gutting the file.
- **Discovered by:** Dogfooding 006
- **Resolution:** `build_target_files_block()` now annotates `.py` files inside Python
  packages (directories containing `__init__.py`) with structural context extracted via
  `ast.parse()`: module docstring (truncated to 80 chars) and top-level function/class
  names (capped at 8). Format: `path  # docstring | name1(), ClassName`. Package
  detection runs before path truncation so late-alphabet packages are still recognized.
  10,000-char annotation budget prevents token bloat. Graceful degradation on `OSError`,
  `SyntaxError`, or `UnicodeDecodeError`. 17 new tests.

### ✅ [2026-07-08] Dogfooding 006 — D-006: Executor writes tool-call markup as file content (RESOLVED)

- **File:** `src/orchestrator/agents/executor/validation.py`, `applier.py`
- **Debt:** When the executor's LLM output is non-Python content (XML tool-call markup,
  prose), it was written to staging as valid code. Now `ast.parse()` validates
  `.py` file content before diff generation; syntactically invalid output is
  rejected with `ERROR` status immediately.
- **Discovered by:** Dogfooding 006 (T1 — scheduler.py replaced with `<tool_call>` markup)
- **Resolution:** Pre-diff `ast.parse()` validation in `validation.py`, gated on `.py`
  extension. Only rejects when original parses but modified does not (avoids false
  positives on files with pre-existing syntax errors).
- **Known limitation:** Catches syntactically invalid content only. Semantically wrong
  but syntactically valid replacements remain undetected until ruff/pytest.

### ✅ [2026-07-08] Dogfooding 007 — Executor cannot create new files (RESOLVED)

- **File:** `src/orchestrator/agents/executor/` (general)
- **Debt:** When `files_to_modify` in the plan lists a path that does not exist, the
  executor fails immediately with "File not found" and marks the task `ERROR`. The
  architect correctly plans new test files (e.g. `test_validator_summarizer.py` in D-007,
  `test_executor_observability.py` in D-006) but the executor rejects them. New-file
  creation requires a dedicated executor code path (write from scratch vs. read-diff-apply).
- **Discovered by:** Dogfooding 006 (T7), confirmed again in Dogfooding 007 (T2).
- **Resolution:** [2026-07-09] Issue #210 — `_apply_task()` now detects missing files
  (not on disk, not in staging) and treats them as new-file creation: sets
  `original_content=""`, uses a dedicated `_build_create_prompt()`, and generates
  `--- /dev/null` diffs via `_make_diff(is_new_file=True)` for `git apply` compatibility.
  Files already in staging from a prior task are treated as accumulated modifications.
- **Known limitation:** `validate_python_content(modified, "", filename)` silently skips
  syntax validation for new `.py` files because `ast.parse("")` raises `SyntaxError`,
  making the function interpret "original was already broken." Consistent with existing
  design contract but a real coverage gap for new files.
- **Correction [2026-07-10]:** This claim is invalid and was never true. `ast.parse("")`
  does **not** raise `SyntaxError` — an empty string parses as a valid, empty module.
  Moreover, `applier.py:157` never passes the literal empty string to
  `validate_python_content()` for new files — it substitutes `"# new file\n"`
  (`validation_original = "# new file\n" if is_new_file else original_content`), which
  was added in the same PR (`fix(executor): configure git identity in test and enable
  syntax validation for new .py files`, commit `11c3547`). Syntax validation for new
  `.py` files works correctly today; this note went stale immediately after its own fix
  landed and was never removed. See `tests/test_executor.py::test_apply_task_rejects_invalid_new_file`
  for the regression test locking in this behavior.

### ✅ [2026-07-08] Dogfooding 007 — LLM adds new CB block instead of extending _call_chain (RESOLVED)

- **File:** `src/orchestrator/agents/validator/summarizer.py`
- **Debt:** When the issue says "add Claude as fallback," the executor LLM copies the
  existing Gemini try/except pattern and reuses `_cb_validator` for Claude instead of
  extending the existing `_call_chain([_call_openrouter], ...)` call. The bloated
  implementation breaks `test_validator_uses_raw_stderr_when_cb_open` (CB called twice,
  test expects once). The minimal correct fix is a one-argument extension:
  `_call_chain([_call_openrouter, _call_claude], ...)`.
- **Discovered by:** Dogfooding 007
- **Resolution (Issue #205, PR #206):** Applied the minimal fix by hand instead of via
  the executor LLM: extended the import and the `_call_chain([...])` list with
  `_call_claude`, and fixed a model-tag misattribution found during adversarial review
  (`chain_result.provider_name` instead of a hardcoded `"openrouter/free"` string, which
  was wrong whenever OpenRouter's fallback was actually served by a different provider).
  Confirms the AC-quality lesson below did not need to be re-litigated once the exact
  construct to modify was named up front.
- **Lesson:** ACs for minimal-edit issues should name the exact construct to modify
  ("extend the existing `_call_chain(...)` call"), not just describe the desired
  behavior. Apply to future issues in the validator fallback area.

### [2026-07-08] Issue #205 — Docstring coverage bot check (60% < 80% threshold)

- **File:** repo-wide (CodeRabbit "Docstring Coverage" check)
- **Debt:** CodeRabbit's automated docstring coverage check reports 60.00% coverage
  against an 80.00% threshold, flagged as a warning on PR #206. The gap is pre-existing
  and repo-wide, not introduced by PR #206 (a 2-file, 3-line summarizer fix).
- **Discovered by:** CodeRabbit bot review on PR #206
- **Why deferred:** Raising repo-wide docstring coverage 20 points is a large,
  unscoped effort unrelated to the validator fallback fix. Out of scope per the
  Golden Rule (smallest correct change). Revisit as a dedicated docs/hardening issue.

### ✅ [2026-06-14] Issue #100 — Agent fallback inconsistency (RESOLVED)

- **File:** `src/orchestrator/agents/validator/summarizer.py` (stale path: was `validator.py`)
- **Debt:** The executor now uses a resilient, unified fallback chain via _call_chain().
  However, the validator agent still uses a primitive, manual fallback (returning
  raw stderr) when Gemini is unavailable. This creates an architectural
  inconsistency and leaves the validation stage less resilient than the execution stage.
- **Discovered by:** Implementation of Issue #100
- **Resolution:** `summarizer.py:89` now uses `_call_chain([_call_openrouter, _call_claude], ...)`
  imported from `executor.providers` (PR #206). The raw-stderr fallback when the
  circuit breaker is open is preserved intentionally — covered by
  `test_validator_uses_raw_stderr_when_cb_open`. Residual minor debt: `_call_chain`
  is a `_`-prefixed private symbol consumed cross-package; not a public API.
