# Experiment: Dogfooding 010 — P4 (Trust & Configuration) end-to-end validation

**Date:** 2026-07-17
**Target:** PatchForge codebase at `62f96c7` (main, post-P4 — Approval Provenance merged)
**Issue:** P4 (Qualitative Risk Gates #226, IssueContract ADR #228, Provider Registry #230, Audit Bundle Export #232/#234/#235/#236, Approval Provenance #241) had only been exercised through mocked unit tests — never against a real repo, real LLM providers, or a real audit bundle.
**Fix under test:** N/A — this is a validation dogfooding, not a bug-fix dogfooding. No product code was modified in the main PatchForge repo; all runs targeted a disposable clone.
**Run IDs:** `run_20260717_011605_fc3bad` (A), `run_20260717_011746_1a9eb3` (A2), `run_20260717_011837_5e575d` (B — abandoned, see below), `run_20260717_014552_c4b97d` (B2 — golden path), `run_20260717_013534_596209` (C)
**Provider:** architect always `claude-sonnet-4-6` (see D-010b); executor pinned to `gemini-2.5-pro` via `orchestrator.json`, fell back to `openrouter/free` (Gemini free tier has 0 daily quota) and, for the final successful executor pass, `--force-provider claude`
**Budget:** ~$0.26 total (architect calls ≈ $0.157 across 5 plan invocations; executor ≈ $0.087 on an abandoned 4-task forced-Claude attempt + $0.011 on the final 1-task forced-Claude apply)

**Scope note:** IssueContract ADR (#228) is a DTO documented in `docs/adr/ADR-0005-issue-contract.md` with no pipeline wiring yet (no adapter consumes it) — it is not runtime-exercisable and is **not** covered by this dogfooding. The other four P4 items are covered.

## Locations

```text
PatchForge   : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target       : %TEMP%\patchforge-dogfooding-010\target\        (fresh clone, main@62f96c7)
Issue files  : %TEMP%\patchforge-dogfooding-010\issues\*.md
Workspace A/A2/B/B2 : %TEMP%\patchforge-dogfooding-010\workspace\
Workspace C  : %TEMP%\patchforge-dogfooding-010\workspace-c\
Audit bundle : %TEMP%\patchforge-dogfooding-010\audit\audit-run_20260717_014552_c4b97d.tar.gz
```

## Setup

Fresh clone from local PatchForge repository (`main`@`62f96c7`) to `%TEMP%\patchforge-dogfooding-010\target`, with `.env` copied for provider access. `orchestrator.json` was created at the clone root pinning `providers.gemini.model = "gemini-2.5-pro"`, to exercise Provider Registry (#230). `patchforge` itself was invoked from the main repo's `.venv\Scripts\patchforge.exe` against the target clone (this is how the tool is meant to be used — one installed PatchForge operating on arbitrary target repos).

Four issues were used across the four runs:
- `issue-scripts.md` — add an optional `OPENROUTER_API_KEY` check (+ tests) to `scripts/bootstrap_check.py`. Under `scripts/`, a `medium` taxonomy tier (`FILE_TAXONOMY`).
- `issue-lowpath.md` — fix a stale exception list in the module docstring of `src/orchestrator/exceptions.py`. No taxonomy rule matches this path (only `src/orchestrator/schemas/` does), isolating the "code-gen floor" rule.
- `issue-scripts-v2.md` — same as `issue-scripts.md` but explicitly scoped to one file, no new tests, used to get a clean golden-path run after `issue-scripts.md` proved unreliable for test-file generation (see Analysis).
- `issue-high.md` — add a docstring to `ProvidersConfig` in `src/orchestrator/schemas/config.py`. Always `high` taxonomy tier.

## Pipeline Results

| Run | Step | Result | Detail |
|-----|------|--------|--------|
| A | `scan --risk-budget low` | ✅ | `v1_supported` was initially `false` (ruff/pytest not on PATH in the venv-less clone — see D-010a); resolved by prepending the main repo's `.venv\Scripts` to PATH |
| A | `plan --issue-file issue-scripts.md` | ❌ blocked (expected) | 2 tasks escalated to `medium` via taxonomy (`scripts/`); gate failed: `budget == "low"` + plan modifies 4 files > `max_files=2` |
| A2 | `scan --risk-budget low` | ✅ | |
| A2 | `plan --issue-file issue-lowpath.md` | ❌ blocked (expected) | 1 task on `src/orchestrator/exceptions.py` (no taxonomy match) escalated `low → medium` purely by the code-gen floor (`risk.py:127-129`); gate failed on `budget == "low"` |
| B | `scan --risk-budget medium` | ✅ | |
| B | `plan --issue-file issue-scripts.md` | ✅ | 4 tasks, 4 files planned (architect used `claude-sonnet-4-6` despite `orchestrator.json` pinning Gemini — see D-010b) |
| B | `preview` (×3 attempts) | ❌ | Attempt 1: T-01 executor error, 3 tasks skipped as dependents. Attempt 2: T-01/T-03/T-04 applied, T-02 (test file) executor error. Attempt 3 (`--force-provider claude`): all 4 tasks applied, but `pytest` failed — Claude's new test imported a nonexistent `orchestrator.bootstrap_check` module instead of following the existing subprocess-based pattern in the same test file. **Run B abandoned** — see Analysis |
| B2 | `scan --risk-budget medium` | ✅ | fresh run, scoped issue |
| B2 | `plan --issue-file issue-scripts-v2.md` | ✅ | 1 task, 1 file; gate passed |
| B2 | `preview` | ❌ then ✅ | Attempt 1 (default routing, fell to `openrouter/free`): executor error, "LLM output is not valid Python (line 1)" — see D-010c. Attempt 2 (`--force-provider claude`): ✅ PASSED (ruff clean, 835 passed/5 skipped) |
| B2 | `apply` | ⚠️ then ✅ | Attempt 1 timed out client-side during the post-apply validator re-run (~3 min for the full pytest suite) mid-flight, leaving the target dirty on the new branch; cleaned up and retried — see D-010e. Attempt 2: ✅ applied to `patchforge/run_20260717_014552_c4b97d`, `triggered_by`/`approved_by` both resolved to local git identity |
| B2 | `export-audit --redact` | ✅ | 13 artifacts bundled; `triggered_by`, `approved_by`, `risk_budget` unredacted in manifest (public fields); `target_path`, `workspace_path`, `provider_config` show `"[REDACTED]"` |
| B2 | `verify-audit` (no flags) | ✅ | "Bundle verified successfully" |
| B2 | `verify-audit --require-signature` | ✅ correctly FAILED | exit code 6, "no manifest.json.asc is present in the bundle" — bundle was never signed (no local GPG secret key available) |
| C | `ci --risk-budget high` on `issue-high.md` | ❌ blocked (expected) | `scan` succeeded, `plan` gate failed: `taxonomy: src/orchestrator/schemas/config.py → high (was low)` then `"is high-risk. High-risk tasks are not applicable in V1."` — confirms `--risk-budget high` has no effect over `medium` (see D-010d) |

## Analysis

### Qualitative Risk Gates (#226) — confirmed working end-to-end

Both escalation paths fired correctly against real files: taxonomy (`scripts/` → `medium`, `src/orchestrator/schemas/` → `high`, Run A/C) and the code-gen floor (`exceptions.py`, no taxonomy match, still escalated `low → medium` for being a `.py` file, Run A2). The budget comparison (`low` blocks `medium`, `medium` passes `medium`) worked as designed. However, `--risk-budget high` produced an **identical outcome to `medium`** in Run C — see D-010d.

### Provider Registry (#230) — only half-wired

`orchestrator.json`'s `providers.gemini.model = "gemini-2.5-pro"` pin was honored by the **executor** (`preview`/`apply`, confirmed via `RunMetadata.model_metadata.executor.models_resolved` and `provider_config` in every run) but silently **ignored by the architect** (`plan` always used `claude-sonnet-4-6` regardless of the pin) — see D-010b. This is a real, non-obvious gap in a feature meant to give operators full model control.

Separately: Gemini's free tier has a **hard 0 daily-request quota** for `gemini-2.5-pro` (`RESOURCE_EXHAUSTED`, `limit: 0`) — every executor call that tried the pinned Gemini model failed immediately and fell through to `openrouter/free`. This isn't a PatchForge bug, but it means the "pin a specific paid-tier model" story doesn't degrade gracefully when that tier has no free quota — worth a `doctor` check or a warning in `orchestrator.json` docs.

### Audit Bundle Export (#232/#234/#235/#236) — confirmed working end-to-end

`export-audit --redact` produced a correctly redacted manifest: `triggered_by`, `approved_by`, and `risk_budget` (public, per `_PUBLIC_FIELDS`) were visible in plaintext, while `target_path`, `workspace_path`, and `provider_config` showed `"[REDACTED]"`. `verify-audit` passed on the unsigned bundle without `--require-signature`, and correctly **failed** (exit 6) when `--require-signature` was passed against the same unsigned bundle — the security-invariant path (not just the happy path) is exercised and correct.

### Approval Provenance (#241) — confirmed working end-to-end

`RunMetadata.triggered_by` resolved to `local:Argenis Lopez <...>` at `scan` time (no `GITHUB_ACTOR` in a local shell), and `approved_by` was populated only at `apply` time with the same local git identity — matching the documented "approval happens at apply, not at scan/ci" design. Both fields survived redaction intact and were visible in the exported audit manifest.

### LLM/executor behavior — reproducible fence-stripping gap (D-010c) and one-off test-writing mistake

Across 5 preview attempts in this session, 3 failed with `"LLM output is not valid Python (line 1): invalid syntax"` whenever routing fell through to `openrouter/free` (the only free option once Gemini's 0-quota kicks in). The executor's prompt explicitly instructs "do NOT include markdown code fences" (`applier.py:28,51`), but there is no code that strips fences defensively if the model ignores that instruction anyway — see D-010c. Forcing `--force-provider claude` reliably avoided this failure mode.

Separately (not a product bug): in the abandoned Run B, Claude's own generated test for `bootstrap_check.py` imported a nonexistent `orchestrator.bootstrap_check` module instead of reusing the `subprocess`-based pattern already present elsewhere in the same test file (`tests/test_baseline_cli.py` imports `orchestrator.main.app`, not the scripts directory) — a plausible LLM oversight, correctly caught by the `pytest` validation gate before it could reach `apply`.

## Discoveries

### D-010a — Scanner's ruff/pytest availability check breaks on venv-less clones

- **File:** `src/orchestrator/scanners/python.py:53-55` (`_detect_tool`)
- **Behaviour:** `_detect_tool()` uses `shutil.which(cmd)` to decide `v1_supported`. A fresh clone with no local `.venv` (the exact scenario dogfooding-009 fixed for the *validator*) fails this scan-time check even when the same Python interpreter running PatchForge could invoke `ruff`/`pytest` via `-m`.
- **Impact:** `scan` exits 1 on any target repo that doesn't have `ruff`/`pytest` on `PATH`, even though the validator (post-#009) no longer has this limitation. Operators must manually prepend a venv's `Scripts`/`bin` dir to `PATH` before scanning.
- **Discovered by:** Dogfooding-010, Run A (first invocation)
- **Why deferred:** Out of scope for P4; the dogfooding-009 fix (`sys.executable -m <tool>`) pattern should likely be applied to `_detect_tool` too, but that's a scanner change, not something P4 touched.

### D-010b — Provider Registry (#230) is not wired into the architect stage

- **File:** `src/orchestrator/agents/architect/provider.py:19-33` vs. `src/orchestrator/agents/executor/providers.py:49-63`
- **Behaviour:** `init_provider_models(config)` — the function that resolves `orchestrator.json`'s `providers.*.model` pins — is called only from `agents/executor/__init__.py:96` and `agents/validator/__init__.py:44`. The architect's `provider.py` has its own hardcoded `_ARCHITECT_CHAIN = [_call_claude, _call_gemini, _call_openrouter]` and hardcoded `_MODEL_MAP`, and never reads `TargetConfig.providers` at all.
- **Impact:** Pinning a model in `orchestrator.json` has zero effect on `plan` — confirmed directly in Runs A/A2/B/B2/C, where `plan` always used `claude-sonnet-4-6` regardless of the Gemini pin. Only `preview`/`apply` (executor) and the validator's LLM summary honor Provider Registry.
- **Discovered by:** Dogfooding-010, Runs A through C (architect log line `Asking claude-sonnet-4-6...` in every run despite the Gemini pin)
- **Why deferred:** Out of scope for this dogfooding; requires a product decision on whether the architect should honor Provider Registry too (likely yes) before scoping a fix issue.

### D-010c — Executor has no markdown-fence-stripping fallback for LLM output

- **File:** `src/orchestrator/agents/executor/applier.py:28,51` (prompt), `src/orchestrator/agents/executor/diffing.py` (no post-processing found), `src/orchestrator/agents/executor/validation.py:19` (`ast.parse` rejects the result)
- **Behaviour:** The executor prompt instructs the model not to wrap output in ` ``` ` fences, but there is no defensive stripping if it does anyway. When routing falls to a weaker/free model (here, `openrouter/free`, the only fallback once Gemini's paid-tier-only quota is exhausted), this produced a reproducible `"LLM output is not valid Python (line 1): invalid syntax"` failure in 3 of 5 preview attempts in this session.
- **Impact:** Wasted LLM calls and pipeline retries whenever the fallback chain reaches a less-compliant model. The `ast.parse` safety net correctly prevents bad content from being applied, but there's no recovery — the task just errors out.
- **Discovered by:** Dogfooding-010, Run B (attempts 1-2) and Run B2 (attempt 1)
- **Why deferred:** Out of scope for P4/this dogfooding; a stripping helper (e.g. detect and strip a leading/trailing ` ``` ` line before `ast.parse`) would likely fix most cases cheaply.

### D-010d — `--risk-budget high` is functionally a no-op vs. `medium`

- **File:** `src/orchestrator/risk.py:131-141` (`check_plan_gate`), `src/orchestrator/main.py:120-136,238-265` (CLI validation)
- **Behaviour:** `check_plan_gate` unconditionally blocks any `risk_level == "high"` task ("High-risk tasks are not applicable in V1"), regardless of `risk_budget`. The only budget comparison in the gate is `medium-risk task vs. budget == "low"`. The CLI accepts `high` as a valid `--risk-budget` value on both `scan` and `ci`, but nothing in the gate logic treats it differently from `medium`.
- **Impact:** Confirmed directly in Run C — `ci --risk-budget high` on a `schemas/` file blocked with the exact same "not applicable in V1" message as `medium` would. The CLI's `--risk-budget high` option is misleading: it implies a higher-risk tier is unlockable, but no such tier exists yet.
- **Discovered by:** Dogfooding-010, Run C
- **Why deferred:** Product ambiguity (should V1 ever support high-risk under `budget=high`, or should the CLI stop accepting `high` as a value?) — not an implementation bug, needs a scoping decision before a fix issue.

### D-010e — Interrupted `apply` leaves the target in a state that misclassifies as `CONFLICT` on retry

- **File:** `src/orchestrator/commands/apply.py:191-227` (lifecycle classification), `:404` (post-apply validator re-run)
- **Behaviour:** `apply` re-runs the full validator (`ruff` + `pytest`, ~3 minutes on this repo) after applying the patch and before committing, with no progress output during that window. An `apply` process killed mid-validator-rerun leaves the target repo on the new `patchforge/<run_id>` branch with the patch already written to the working tree but uncommitted. Retrying `apply` on that same state fails with `Patch lifecycle state is CONFLICT... HEAD <sha> has diverged from base commit <sha>` — even though the two SHAs printed in the error message are **identical**, because the classifier doesn't account for "already-applied-but-uncommitted, matching the pending patch" as a distinct, resumable state.
- **Impact:** An operator whose `apply` run is interrupted (client timeout, killed process, CI cancellation) cannot simply retry — they must manually reset the target branch (`git checkout -- <file>`, switch back to base, delete the stray branch) before `apply` will proceed again. The error message itself is also misleading (same-looking SHA reported as "diverged").
- **Discovered by:** Dogfooding-010, Run B2 (`apply` attempt 1, client-side 2-minute timeout mid-validator-rerun)
- **Why deferred:** Out of scope for P4; worth its own issue (either make `apply` resumable from a matching dirty state, or make the CONFLICT message clearer when the two SHAs are actually equal).

## Verdict

**PASS**, with five documented gaps. Qualitative Risk Gates, Audit Bundle Export, and Approval Provenance all behaved exactly as designed against a real repo, real git history, and (for the audit chain) both the happy path and the security-invariant path (`--require-signature` correctly rejecting an unsigned bundle). Provider Registry works for the executor/validator stage but is completely unwired for the architect stage (D-010b) — the highest-value finding here, since it means "configure your model once" is currently false for `plan`. The other four discoveries (D-010a, D-010c, D-010d, D-010e) are all real but lower-severity: environment-detection strictness, LLM-output robustness, a misleading CLI option, and an apply-retry rough edge. No regressions were found in P3-era behavior. IssueContract ADR (#228) remains unexercised at runtime, as expected (no pipeline wiring exists yet).

No product code was committed as part of this dogfooding — the applied patch lives only on the disposable target clone's `patchforge/run_20260717_014552_c4b97d` branch and was never pushed anywhere.
