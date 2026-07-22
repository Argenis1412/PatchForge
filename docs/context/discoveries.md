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

### [2026-07-22] Dogfooding 011 — Analysis-only task blocks entire DAG (D-011a)

- **File:** `src/orchestrator/agents/architect/` (plan generation), `src/orchestrator/agents/executor/` (task executor, DAG scheduler)
- **Debt:** An issue file that leaves the implementation approach open-ended (e.g. "the architect should inspect the surrounding code before proposing the fix") produces a plan where T1 is an analysis task that returns prose, not Python. T1 → `ast.parse` failure → all downstream tasks blocked as DAG dependents. The pipeline exits at `preview` with zero patch output and no clear message that the issue file was the root cause.
- **Discovered by:** Dogfooding-011, Run A (`issue-failure-json.md` was deliberately open-ended; produced a 4-task plan with T1=analysis)
- **Why deferred:** Not a product bug — the architect is doing what it's asked. Mitigation is workflow: issue files must name the file and approach explicitly; analysis tasks are not a supported executor output type. Documented as an issue-writing rule.

### [2026-07-22] Dogfooding 011 — Executor file-size ceiling varies by model (D-011b)

- **File:** `src/orchestrator/agents/executor/applier.py` (full-file replacement approach)
- **Debt:** The executor sends the full file content to the LLM and asks for the modified file back. Files above the model's effective output window produce truncated Python → `ast.parse` failure. Observed failure points: haiku failed on ~1000-line files (51K chars); gemini-2.5-flash failed on ~380-line files. The exact ceiling for each model is unknown — these are the largest files tested at failure, not empirically determined limits. The error message (`"unterminated string literal"` or `"invalid syntax"`) is technically correct but gives no hint that the root cause is model output truncation vs. a content error.
- **Discovered by:** Dogfooding-011, Run A2 (haiku + `apply.py` ~1000 lines) and Run C (gemini + `main.py` ~380 lines)
- **Why deferred:** Executor redesign (chunked or diff-only mode) is a significant architectural change, out of scope for the current phase. The file-size ceiling is a documented limitation of the full-file replacement approach noted in `docs/planning/strategic-recommendations.md`.

### [2026-07-22] Dogfooding 011 — Executor applied `typer.Option()` to a non-Typer function (D-011c)

- **File:** `src/orchestrator/commands/plan.py:30` (`execute()` signature), generated patch (Run A3)
- **Debt:** Run A3's patch modified both `main.py` and `plan.py`. In `plan.py`, the executor changed `execute()`'s `workspace` parameter from `Optional[Path] = None` to `Optional[Path] = typer.Option(None, "--workspace", help=...)`. `execute()` is a plain Python function called from `main.py`'s Typer callback — not a Typer command handler — so the `typer.Option()` default is semantically wrong (Typer evaluates it only when registered as a command). The patch passed `pytest` (835 passed) because `main.py` always passes `workspace` explicitly, so the wrong default is never triggered at test time. Any direct caller of `plan.execute()` without `workspace` would receive a `OptionInfo` object instead of `None`.
- **Discovered by:** Dogfooding-011, Run A3 (noted during post-apply diff review; the fix was applied only to the target clone, never to the main PatchForge repo)
- **Why deferred:** Root cause is a multi-file plan where the executor blindly applied a Typer pattern from `main.py` to `plan.py`. Mitigation: issue files should scope to the minimum files needed; single-file plans reduce the risk of cross-file pattern bleeding, though they do not guarantee the executor won't apply the wrong pattern within a single file. The validation gate (pytest) did not catch it because no test calls `plan.execute()` with default `workspace`. A signature regression test was added to `tests/test_plan.py`.

### ⏳ [2026-07-22] Dogfooding 011 — gemini-2.5-flash fallback silently degrades executor for medium-sized files (D-011d) (PARTIALLY RESOLVED, Parts 1-2 of 3)

- **File:** `src/orchestrator/agents/executor/applier.py` (executor), `src/orchestrator/agents/architect/provider.py` (fallback chain)
- **Debt:** When Anthropic API credits are exhausted, both the architect and executor fall back to `gemini-2.5-flash`. For files around 380 lines (`src/orchestrator/main.py`), gemini produced "unterminated string literal (detected at line 81)" on the first attempt and "invalid syntax (line 1)" on a second independent attempt — two different failure modes, suggesting nondeterministic truncation or malformed output. The `ast.parse` gate correctly rejects both, but there is no signal in the output that a credit-exhausted fallback is in use. The operator sees the same executor error messages as D-011b (file too large for the model), with no indication the real cause is the degraded fallback model.
- **Discovered by:** Dogfooding-011, Run C (both attempts; `Done | model=gemini-2.5-flash` confirmed in architect log for both plan calls after Anthropic 402)
- **Why deferred (originally):** Two separate improvements needed: (1) the executor's full-file replacement approach is incompatible with gemini-2.5-flash for medium-sized files — requires executor redesign (same root as D-011b, still fully deferred, see D-011b above); (2) the system logs the final model used at DEBUG level but does not emit a visible warning when the registry-specified or default model was not the one that actually ran.
- **Resolution (Part 1 — issue #274, PR #275, branch `fix/issue-274-provider-fallback-warning`):** Addresses only mitigation (2), and only for the **architect** path (`plan`). `ProviderChainResult` (`src/orchestrator/agents/executor/providers.py`) gained `primary_provider_attempted`/`primary_failure_category`, computed once inside `_call_chain()` from `chain[0]` — the single condition for "a fallback occurred" is `provider_name != primary_provider_attempted`, never inferred from a non-empty `failures` list alone (that would false-positive on a same-provider retry). `failures`'s tuple shape was deliberately left unchanged during implementation to avoid breaking two pre-existing consumers (`agents/architect/provider.py:53`, `agents/executor/applier.py:139`) — a bug caught by re-reading an earlier draft of the plan against the actual code before implementing it. `call_claude()` now returns an `ArchitectCallResult` dataclass instead of a positional 4-tuple. `commands/plan.py` (not `agents/architect/__init__.py` — that module only does plain `print()`, no Rich, so the console/`log_event` wiring was moved to the command layer where the existing `[yellow]Override activo` pattern already lives) prints a `rich.markup.escape()`-guarded `[yellow]` warning and emits a `provider_fallback` event to `pipeline.jsonl` whenever a fallback occurs. Full `/clarify` → `/challenge-ac` → `/adversarial` (×2) → implement trail. Tests: `tests/test_executor.py` (`_call_chain`/`_categorize_failure` unit tests), `tests/test_v1_commands.py` (`plan` CLI integration: warning text, event fields, no-false-positive, Rich markup escaping), plus ~20 pre-existing tests in `test_architect.py`/`test_provider_registry.py` updated for the `call_claude()` return-type change. QA green (966 passed, 6 skipped).
- **Resolution (Part 2 — issue #278, branch `fix/issue-278-executor-fallback-warning`):** Addresses mitigation (2) for the **executor** path (`preview`, `pipeline.py`). `FileChange` gained `primary_provider_attempted`/`primary_failure_category` (both `str | None = None`, backward-compatible with pre-existing persisted `executor_<run_id>.json` files). New shared helper `agents/executor/fallback.py` (`collect_fallback_changes`/`log_fallback_events`) is the single source of truth for "which `FileChange`s count as a fallback" and "log the event for each," reused verbatim by `preview.py` (console print + event) and `pipeline.py`'s `_stage_executor` (event only — no `Console` instance exists in `pipeline.py`). The fallback filter requires **both** `status in {APPLIED, NOOP, PENDING_REVIEW}` **and** `provider_name != primary_provider_attempted` — the status restriction, added during an external `/adversarial` pass, is load-bearing: a fallback provider's response can still fail Python syntax validation and be discarded (`status=ERROR`, `provider_name` still set), and without it the warning would misreport a terminal failure as "fell back, now using X." `pipeline.py`'s `--from-stage executor` resume branch deliberately does **not** re-call `log_fallback_events` — an earlier draft of this plan added it there to avoid "losing" the event on resume, which a later adversarial pass identified as backwards: the event was already emitted once during the original fresh run, and re-emitting on resume would duplicate telemetry under a new `trace_id` and mislabel a skipped stage as active executor work. `pipeline.py`'s `TaskResult.model_used` was also fixed in the same PR (`change.provider_name or "n/a"`, never the aggregate `"GM:...|OR:...|CL:..."` config string it used unconditionally before, and never a mix of provider-shortname and aggregate-string shapes as an early draft of the fix did) — a real audit/provenance gap surfaced by an external adversarial pass, cheap to fix since the same loop and the same `provider_name` data were already being touched. Five `/adversarial` passes total across this issue's planning (two self-review rounds via the `/adversarial` skill, three further external reviews pasted into the session, numbered attacks 1-7 across them) — see items below for what was raised and deliberately rejected as out of scope. Tests: `tests/test_executor.py` (`_apply_task` field-threading + `collect_fallback_changes` filtering, 14 tests), `tests/test_preview_execute.py` (4 tests: warning text, no-false-positive, syntax-error exclusion, Rich markup escaping), `tests/test_pipeline.py` (6 tests: event emission, no-event on no-fallback, no-event on persist failure, no-re-emit on resume, `TaskResult.model_used` accuracy, OSError-tolerance). QA green (988 passed, 6 skipped).
- **Still open (not started):**
  - **Part 3 — `ci.py`**: first needs to confirm by reading the code whether `ci.py`'s planning/execution steps call `agents/architect/__init__.py`/`agents/executor/applier.py` directly (in which case Parts 1-2 already cover both halves via the shared `pipeline.jsonl` event) or reimplement inline — there is documented precedent for `ci.py` reimplementing pipeline steps inline elsewhere in this file (see the #270-era entries below on `ci.py`'s independent HEAD-divergence handling).
  - Mitigation (1) — the actual executor redesign for gemini-compatibility at medium file sizes — remains fully out of scope, same as D-011b.

### [2026-07-22] Issue #278 — retry-loop attempts within a single task are not individually tracked for fallback reporting

- **File:** `src/orchestrator/agents/executor/applier.py:123-137` (`_apply_task`'s `for attempt in range(MAX_RETRIES + 1)` loop)
- **Debt:** `_apply_task` can call `_call_chain()` up to `MAX_RETRIES + 1` times per task. Only the winning attempt's `primary_provider_attempted`/`primary_failure_category` are threaded onto the returned `FileChange` (matching the existing `last_failures` semantics, which already only reflects the last attempt). A task where attempt 1 fully exhausts the provider chain and attempt 2 succeeds on the primary provider directly will report "no fallback occurred," even though the run experienced real distress on the first attempt.
- **Discovered by:** `/adversarial` review during issue #278 planning.
- **Why deferred:** Cross-attempt fallback tracking is a larger, separate scope decision (how to represent "fallback within a retry, then recovery" in a single `FileChange`) than this visibility fix's narrow scope.

### [2026-07-22] Issue #278 — `--force-provider` on a HIGH-risk task bypasses the no-fallback policy invisibly

- **File:** `src/orchestrator/agents/executor/applier.py:109-118` (`force_provider` overriding `chain` selection unconditionally)
- **Debt:** `force_provider` overrides chain selection regardless of `task.risk_level` — including HIGH risk, whose `_PROVIDER_CHAIN["high"] = [_call_claude]` exists specifically so a HIGH-risk task never silently degrades to a less capable model. A forced single-element chain can never look like a fallback under `collect_fallback_changes`'s definition (`provider_name != primary_provider_attempted` is trivially false for a one-element chain), so this risk-policy bypass has no diagnostic trail at all — and could be mistaken for something the new fallback-visibility feature would catch, when it structurally cannot.
- **Discovered by:** `/adversarial` review during issue #278 planning.
- **Why deferred:** Pre-existing (present since before Part 1), unrelated to fallback visibility. Fixing it means deciding whether `--force-provider` should be allowed to bypass risk policy at all, a policy question separate from this issue.

### [2026-07-22] Issue #278 — `TaskResult.model_used`/`FileChange.provider_name` report provider shortname, not the specific model id

- **File:** `src/orchestrator/schemas/pipeline_run.py` (`TaskResult.model_used`), `src/orchestrator/schemas/executor_output.py` (`FileChange.provider_name`)
- **Debt:** Both fields report a short provider name (`"gemini"`, `"openrouter"`, `"claude"`), never the specific model id actually configured/resolved (e.g. `"gemini-2.5-flash"`). This was true before issue #278 (which fixed `TaskResult.model_used`'s *shape* — no longer the aggregate config string — but did not add model-id granularity) and remains true after.
- **Discovered by:** `/adversarial` review (external) during issue #278 planning.
- **Why deferred:** Fixing it means plumbing `_resolved_models` through `_apply_task` and adding a new `model_name`-style field to `FileChange`, a schema expansion beyond this issue's visibility-fix scope.

### [2026-07-22] Issue #278 — neither architect's nor executor's provider fallback is surfaced in `RunMetadata`/`run.json`

- **File:** `src/orchestrator/schemas/artifacts.py` (`RunMetadata`), `src/orchestrator/commands/plan.py`, `src/orchestrator/commands/preview.py`
- **Debt:** Both Part 1 (architect) and Part 2 (executor) emit `provider_fallback` only to `pipeline.jsonl` — `run.json` (`RunMetadata`) has no `executor_had_fallback`-style flag or structured per-task fallback record, even though it already has `executor_had_errors` for a symmetrical concept. An automated compliance/audit process that reads only `run.json` (not `pipeline.jsonl`) from an `export-audit` bundle would have no way to know a fallback occurred.
- **Discovered by:** `/adversarial` review (external) during issue #278 planning.
- **Why deferred:** Not a regression from #278 — the same gap already existed for architect after Part 1 and was not flagged as blocking then. Fixing it symmetrically for both stages requires designing a per-task fallback record shape for `RunMetadata`, a larger schema-and-audit-contract change than either Part 1 or Part 2's scope. Candidate for a future dedicated part covering both stages together.

### [2026-07-22] Issue #278 — resuming a pre-Part-2 `executor_<run_id>.json` now shows `TaskResult.model_used="n/a"` instead of the aggregate config string

- **File:** `src/orchestrator/pipeline.py` (`_apply_executor_results`)
- **Debt:** `TaskResult.model_used` is now `change.provider_name or "n/a"` rather than the run-level aggregate `"GM:...|OR:...|CL:..."` string. Resuming (`--from-stage executor`) a run persisted before issue #278, whose `FileChange`s never had `provider_name` populated, now surfaces `"n/a"` for those tasks instead of the aggregate string. Not a regression — the aggregate string was itself an inaccurate per-task attribution (the real per-task provider was never persisted in those old runs) — but an operator resuming old runs should not read `"n/a"` as a bug.
- **Discovered by:** `/adversarial` review (external) during issue #278 planning.
- **Why deferred:** No migration path added, following the precedent set by Part 1 (which introduced an equivalent shape change to `architect_output.json` without a migration).

### [2026-07-22] Issue #278 — `--force-provider` remains blocked by an `OPEN` circuit breaker with no operator override

- **File:** `src/orchestrator/agents/executor/providers.py:187-189` and equivalents (`_call_gemini`/`_call_openrouter`/`_call_claude`, each coupling directly to its `CircuitBreaker`)
- **Debt:** `--force-provider <provider>` still goes through that provider's shared `CircuitBreaker` in `coordination.db`. If the CB is `OPEN` (e.g. from unrelated prior failures on another worker), `--force-provider` fails immediately with `CircuitBreakerOpenError` and — because the forced chain is single-element — aborts as "chain exhausted." The operator's explicit override can't proceed until the CB cools down or is manually reset in SQLite.
- **Discovered by:** `/adversarial` review (external) during issue #278 planning.
- **Why deferred:** Pre-existing (present since before Part 1), unrelated to fallback visibility. Fixing it means deciding an operator-override policy against shared protective state (options range from a `--break-circuit` opt-in to a separate `patchforge circuit-breaker reset` command) — its own `/clarify` pass, not a visibility fix.

### [2026-07-22] Issue #274 — pre-existing raw exception text already persisted to `pipeline.jsonl` for total chain exhaustion, inconsistent with the new fallback event's sanitization

- **File:** `src/orchestrator/agents/architect/provider.py:54-66` (`call_claude()`'s `log_failure()` call on full chain exhaustion)
- **Debt:** When the entire provider chain is exhausted (not just a partial fallback), `call_claude()` already builds `failures = "; ".join(f"{n}→{e}" for n, e in chain_result.failures)` from raw `str(exc)` text and passes it verbatim into `log_failure()`, which persists it to `pipeline.jsonl`. Issue #274's new `provider_fallback` event (partial-fallback-success case) deliberately never persists raw exception text — only a closed-set category — specifically to avoid non-deterministic content in a persisted artifact and to avoid feeding untrusted text into a Rich `console.print` markup string. This pre-existing total-exhaustion path was left as-is; the two paths are now inconsistent (one sanitized, one not) for what is conceptually the same class of event.
- **Discovered by:** `/adversarial` review during issue #274 planning.
- **Why deferred:** Pre-existing behavior, not introduced or worsened by #274 — out of scope for a Part-1, architect-only visibility fix. Revisit if `pipeline.jsonl`'s inclusion in `export-audit` bundles (see next entry) makes this a real disclosure concern, or as part of a future consistency pass across all `log_failure`/`log_event` call sites in the provider chain.

### [2026-07-22] Issue #274 — unverified whether `pipeline.jsonl` (and its raw-text content) is included in `export-audit` bundles without redaction

- **File:** `src/orchestrator/commands/export_audit.py`
- **Debt:** `export-audit`'s existing redaction mechanism (issue #232/#234) classifies `RunMetadata` fields as redact-worthy or public, enforced by an anti-rot test. It is not confirmed whether `pipeline.jsonl` itself (which may contain raw exception text — see the entry above) is bundled verbatim into audit exports, and if so, whether that already bypasses the redaction mechanism entirely for anything logged via `log_event`/`log_failure`.
- **Discovered by:** `/adversarial` review during issue #274 planning.
- **Why deferred:** Verification-only task, broader than a Part-1 architect-only visibility fix; requires reading `export_audit.py`'s bundle-construction code to answer. Flagged here so a future session doesn't have to rediscover the question.

### [2026-07-22] Issue #274 — `_compute_cost()` reports `$0.00` for non-Claude fallback providers, unrelated to but adjacent to the new fallback warning

- **File:** `src/orchestrator/agents/executor/providers.py:134-148` (`_compute_cost()`)
- **Debt:** `_compute_cost()` returns `0.0` unconditionally for any provider other than `_call_claude` — meaning every gemini/openrouter fallback (including the ones the new `provider_fallback` warning now surfaces) is already reported as free. The new visibility feature makes the *fact* of a fallback visible but does not correct the *cost* silently going to zero in the same moment, which is arguably part of "what changed when we fell back" from an operator's perspective.
- **Discovered by:** `/adversarial` review during issue #274 planning.
- **Why deferred:** Pre-existing simplification, not introduced by #274; correcting it means adding real gemini/openrouter cost tables, a separate scope decision from visibility.

### [2026-07-19] Issue #258 (all parts closed) — crash-window gap in dirt capture is inherent to the current design, not fixed

- **File:** `src/orchestrator/commands/apply.py` (`--allow-dirty` dirt capture: `stash_create_dirt` + reset onto the target branch), `src/orchestrator/git.py` (`stash_create_dirt`, `store_dirt_ref`)
- **Debt:** If the process crashes after dirt is captured (Part 3, issue #262) but before the apply finishes, the captured dirt sits in a private ref (`refs/patchforge/dirt/{run_id}`, Part 3.5) that the user doesn't know about until the startup advisory catches it on a later invocation. This is inherent to the current approach ("Camino A": reset the main working tree in place, capture dirt as a git object) rather than a bug — a stronger guarantee would require resetting/mutating a separate `git worktree add` checkout instead of the user's main tree, so the main tree is never in a transiently-dirt-free state to begin with. Discussed and deliberately deferred during Part 3's adversarial review as a larger architectural change, reaffirmed out of scope during Part 4 (issue #266). Not blocking for closing #258 — the orphan-advisory (Part 3.5/4) mitigates it by surfacing orphaned refs on the next invocation.
- **Discovered by:** Part 3 adversarial review (originally tracked in `docs/context/plan-issue-258-resumable-apply.md`, removed after all 4 parts of #258 merged — this entry preserves the deferred idea so it isn't lost).
- **Why deferred:** Larger architectural change (worktree-based apply execution) than any single part of #258's scope; revisit only if the crash-window gap proves to matter in practice (no data-loss incident reported so far, only a UX gap bridged by the orphan advisory).

### [2026-07-19] Issue #258 close-out — `classify_lifecycle`'s consumption of `working_tree_equals_expected_state` is only ever tested against a mock

- **File:** `tests/test_lifecycle.py` (mocks `orchestrator.lifecycle.working_tree_equals_expected_state` in all `_probe_already_applied` tests), `tests/test_apply_resumable.py` (new `test_working_tree_check_detects_real_content_divergence` / `test_working_tree_check_ignores_mode_only_difference`, real/unmocked)
- **Debt:** The two regression tests closing out #258's remaining AC exercise `working_tree_equals_expected_state` (`src/orchestrator/git.py`) directly, with real git subprocesses, proving the `core.filemode=false` diff-files check correctly ignores mode-only differences while still catching real content divergence. They do not go through the actual caller, `_probe_already_applied` → `classify_lifecycle` (`src/orchestrator/lifecycle.py:79-113`), which is exercised only against a mocked `working_tree_equals_expected_state` in `test_lifecycle.py`. A regression in the wiring between the two (e.g. an argument passed in the wrong order or a return value inverted) would not be caught by either test file today.
- **Discovered by:** `/challenge-ac` and `/adversarial` passes during planning for the #258 close-out.
- **Why deferred:** Pre-existing gap, not introduced by this change — `test_lifecycle.py` already mocked this function before these tests were added. Closing it would mean adding a real (non-mocked) end-to-end test through `classify_lifecycle`, which is a larger, separate scope decision (how much of `test_lifecycle.py`'s mocking strategy to unwind) than the two narrowly-scoped regression tests #258's AC actually asked for.

### [2026-07-19] Issue #266 (Part 4 of #258) — `run_metadata.dirt_stash_sha` can diverge from `wal_result.dirt_stash_sha` on a specific crash window

- **File:** `src/orchestrator/commands/apply.py` (happy-path dirt capture, around the `force_reset_apply` call and the `run.json` write that follows it)
- **Debt:** If the process crashes between `force_reset_apply` succeeding (dirt already captured and referenced, tree already reset to clean) and the subsequent `workspace_mgr.write_run_json(run_id, run_metadata)` call that persists `dirt_stash_sha` to `run.json`, the two on-disk sources of truth diverge: the ref (`refs/patchforge/dirt/{run_id}`) and the first WAL checkpoint (`apply.json`, written later in the same happy-path pass) both end up with the correct SHA, but `run.json`'s `run_metadata.dirt_stash_sha` stays `None`. Part 4's sub-case 0 detection (`git.resolve_dirt_ref`) tolerates this correctly — its SHA-mismatch abort only fires when `run_metadata.dirt_stash_sha` is *not* `None` and disagrees, so a `None` value is treated as "not yet recorded" rather than a false mismatch, and the ref still gets reused. The only observable effect is degraded messaging in an already-narrow follow-on scenario: if sub-case 0 reuse then *also* crashes in sub-case 2's window (dirt restored, WAL not yet finalized), the CONFLICT-branch re-check in apply.py keys off `run_metadata.dirt_stash_sha`, which by then is populated by sub-case 0's own write — so in practice this only matters for a triple-crash sequence that never reaches that far. No data-loss path identified.
- **Discovered by:** `/adversarial` review during Part 4 planning (documented in the pre-implementation plan as "hallazgo 6", confirmed pre-existing since Part 3, not introduced or worsened by Part 4).
- **Why deferred:** Narrow crash window, no data loss, and closing it would require moving the `run.json` write earlier or making it atomic with the ref creation — a change to Part 3's happy-path dirt-capture sequencing that is out of scope for Part 4 (Option E deliberately touches only the resume/reuse paths, not the original capture sequence).

### ✅ [2026-07-17] Issue #252 (PR #253) — Module-probe cwd is a world-writable shared temp dir (RESOLVED in #257)

- **File:** `src/orchestrator/tool_probe.py:21-26` (`_PROBE_CWD`, used by `_probe_module`)
- **Debt:** `_PROBE_CWD = Path(tempfile.gettempdir())` moves the `sys.executable -m <tool> --version` probe out of the scanned repo's directory (issue #250's fix for a malicious `ruff.py` at the repo root shadowing the real package), but the destination — the OS shared temp directory (e.g. `/tmp` on Linux/macOS) — is itself often world-writable. Since `python -m` prepends the process's cwd to `sys.path`, another local user could plant a `ruff.py`/`pytest.py` in that shared temp dir and have it imported and executed under the probing process's account instead of the real installed package (CWE-427, uncontrolled search path).
- **Discovered by:** CodeRabbit bot review on PR #253 (unrelated `#252` unification work moved this pre-existing code from `scanners/python.py` without changing it).
- **Resolution:** Issue #256 replaced `_PROBE_CWD` with a private, per-probe scratch directory created via `tempfile.TemporaryDirectory(prefix="probe_", ignore_cleanup_errors=True)` used as a context manager inside `_probe_module`, so creation and cleanup are both handled by the stdlib instead of a shared, world-writable directory. `tests/test_tool_probe.py` adds a CWE-427 regression test that plants a shadow module in a simulated shared temp dir (isolated via `monkeypatch`, never the real OS temp dir) and asserts it is never imported.

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
- **Resolution:** Issue #236 added a CI step that generates an ephemeral RSA-2048 keypair in an isolated `GNUPGHOME` (via `mktemp -d`, exported through `GITHUB_ENV`) before the test steps run. A post-generation check (`gpg --list-secret-keys | grep -q sec`) fails the step loudly if the key wasn't actually usable, preventing a silent regression back to skip-only behavior. Accepted risk: keyring contention under parallel test execution is avoided today because `pytest -n auto --dist loadfile` groups same-file tests onto one xdist worker, not because of an explicit lock; flagged for revisit if CI flakiness appears or the dist strategy changes.
- **Bug caught by this fix:** `test_gpg_mutated_signature_fails_verify` simulated tampering by appending `b"\nMUTATED\n"` after `-----END PGP SIGNATURE-----`. gpg's armor parser stops reading at the END marker, so the appended bytes were silently ignored and the original signature still verified successfully — the test asserted a failure that real gpg never produced. Undetected for as long as the test was skip-guarded; surfaced immediately once CI exercised the real binary (first CI run on #236's PR failed with "DID NOT RAISE Exit"). Fixed in the same PR by flipping a byte inside the armored body instead of appending after it, which corrupts the actual signature packet (confirmed against the real gpg binary locally: `gpg --verify` returns exit 2 / "invalid packet").

### ✅ [2026-07-17] Dogfooding 010 — Scanner's ruff/pytest availability check breaks on venv-less clones (RESOLVED in #250)

- **File:** `src/orchestrator/scanners/python.py:100-112` (`_detect_tool`)
- **Debt:** `_detect_tool()` uses `shutil.which(cmd)` to decide `v1_supported`. A fresh clone with no local `.venv` fails this scan-time check even when the same Python interpreter running PatchForge could invoke `ruff`/`pytest` via `-m` — the exact scenario dogfooding-009 already fixed for the *validator* (`sys.executable -m <tool>` in `runners.py`), but that fix never reached this scanner-side detection.
- **Discovered by:** Dogfooding-010, Run A
- **Resolution:** Issue #250 split `_detect_tool` into `_probe_module` (tries `sys.executable -m <tool> --version` first, mirroring the validator's default invocation) and `_probe_path` (the original `shutil.which` + bare-command check, kept as a fallback for `cmd_override` users). Only `rc==0` on the module probe counts as a hit; timeout/OSError/non-zero rc all fall through to PATH, since none of them prove the module isn't importable. This fix does not eliminate the inverse case (tool on PATH but not importable via `-m`) — see the two new discoveries logged below.

### ✅ [2026-07-17] Issue #250 — Scanner and doctor now disagree on tool availability on venv-less clones (RESOLVED in #252)

- **File:** `src/orchestrator/doctor.py:13-32` (`check_command_available`) vs. `src/orchestrator/scanners/python.py` (`_detect_tool`)
- **Debt:** Issue #250 gave `scan`'s tool detection a `sys.executable -m <tool>` probe before falling back to PATH. `doctor.py`'s `check_command_available()` still only checks PATH via a bare command. Confirmed by grep that `doctor.py` does not call `_detect_tool` — the two are independent implementations. Result: on a venv-less clone where ruff/pytest are only importable (not on PATH), `patchforge scan` now reports `v1_supported: true` while `patchforge doctor` still reports the tools as missing.
- **Discovered by:** Implementation of #250 (flagged during adversarial plan review, confirmed by grep before implementation)
- **Resolution:** Issue #252 extracted the probe logic (`_probe_module` + `_probe_path`, including the `_PROBE_CWD` cwd-pin and `PYTHONPATH`-stripped env from #250's shadowing fix) into a new shared module, `src/orchestrator/tool_probe.py`. Both `scanners/python.py::_detect_tool` and `doctor.py::check_command_available` now call into it — no duplicated probe implementation remains in either file. `doctor` keeps its own 30-second timeout (vs. the scanner's 10s default) via an explicit `timeout` parameter, so unification did not silently shrink doctor's timeout budget. `doctor` also inherits the scanner's more lenient PATH-probe semantics: a tool on PATH whose `--version` invocation times out or exits non-zero is now reported available (matching what `scan` already did for a `cmd_override`-style install), which is the intended effect of unification, not an accidental one. `check_ruff`/`check_pytest`'s FAIL messages were updated to mention both probe forms. New regression test asserts `scan` and `doctor` agree on a mocked venv-less clone.

### [2026-07-17] Issue #250 — Scanner cannot distinguish "available via PATH" from "available via the validator's default `-m` invocation"

- **File:** `src/orchestrator/scanners/python.py` (`_detect_tool` / `_probe_path`)
- **Debt:** After #250, `_detect_tool` accepts *either* a successful `sys.executable -m <tool>` probe *or* a PATH-based probe as evidence of availability. This is necessary because the scanner doesn't know whether the validator will run with its default invocation (`-m`) or a user-configured `cmd_override` (typically a bare PATH command) — but it means a tool installed via pipx (on PATH, not importable by the running interpreter) is reported `available: true` by `scan` even though the validator's *default* `-m` invocation would fail against it. `test_detect_tool_falls_back_to_path_when_module_missing` in `tests/test_scan.py` documents this exact scenario.
- **Discovered by:** Adversarial plan review during #250 planning (a first draft of the issue incorrectly claimed this false positive was eliminated; corrected before implementation)
- **Why deferred:** Fixing this properly requires the scanner to read the validator's configured invocation form from `orchestrator.json` (`cmd_override` vs default) — a larger, separate change with its own config-reading surface and test plan. Out of scope for #250.

### ✅ [2026-07-17] Dogfooding 010 — Provider Registry (#230) is not wired into the architect stage (RESOLVED in #246)

- **File:** `src/orchestrator/agents/architect/provider.py:19-33` vs. `src/orchestrator/agents/executor/providers.py:49-63`
- **Debt:** `init_provider_models(config)` — the function that resolves `orchestrator.json`'s `providers.*.model` pins — is called only from `agents/executor/__init__.py:96` and `agents/validator/__init__.py:44`. The architect's `provider.py` has its own hardcoded `_ARCHITECT_CHAIN` and hardcoded `_MODEL_MAP`, and never reads `TargetConfig.providers` at all, so pinning a model in `orchestrator.json` has zero effect on `plan` — confirmed directly across 5 dogfooding runs, all of which used `claude-sonnet-4-6` for `plan` despite a Gemini pin.
- **Discovered by:** Dogfooding-010, Runs A through C
- **Resolution:** Issue #246 (PR #248) wires `init_provider_models(config)` into both `architect.run()`/`run_from_issue()` and `scout.run()`, before their first LLM call. Both agents' local `_MODEL_MAP`/`_COST_RATES` were removed; `model_used` now resolves via the shared `_get_model()`, and cost comes straight from `_call_chain()` (already nullable when a model is overridden, via `_compute_cost()`'s guard) instead of being recomputed against a stale rate table. `log_call` and all cost print statements were made `None`-safe so an overridden model with an unknown cost reports "unknown" instead of a wrong number or a crash. 10 new tests.

### ✅ [2026-07-17] Dogfooding 010 — Executor has no markdown-fence-stripping fallback for LLM output (RESOLVED in #245)

- **File:** `src/orchestrator/agents/executor/applier.py:28,51` (prompt), `src/orchestrator/agents/executor/validation.py:19` (`ast.parse` rejects the result)
- **Debt:** The executor prompt instructs the model not to wrap output in markdown fences, but there is no defensive stripping if it does anyway. When routing falls to a weaker/free model, this produced a reproducible `"LLM output is not valid Python (line 1): invalid syntax"` failure in 3 of 5 preview attempts during this dogfooding session. The `ast.parse` safety net correctly prevents bad content from being applied, but there's no recovery — the task just errors out and burns the call.
- **Discovered by:** Dogfooding-010, Runs B and B2
- **Resolution:** Issue #245 (PR #247) added `strip_fences()` in `validation.py`, called from `applier.py` right before Python syntax validation. Handles ``` and ~~~ fences (with or without language tags, including `c++`/`f#`/`objective-c`), preamble/trailing text around a single fence pair, and preserves inner backticks. Only strips when exactly one complete fence pair is found — ambiguous content (no fences, unclosed, mismatched types, multiple pairs) passes through unchanged. Skips `.md`/`.markdown` files, where fences are legitimate content. `_strip_markdown()` in `providers.py` is left untouched as a documented known limitation (its naive `split()` can corrupt content with inner backticks before `strip_fences` ever sees it). 16 new tests.

### ✅ [2026-07-17] Dogfooding 010 — `--risk-budget high` is functionally a no-op vs. `medium` (RESOLVED in #254)

- **File:** `src/orchestrator/risk.py:131-141` (`check_plan_gate`), `src/orchestrator/main.py:120-136,238-265` (CLI validation)
- **Debt:** `check_plan_gate` unconditionally blocks any `risk_level == "high"` task ("High-risk tasks are not applicable in V1"), regardless of `risk_budget`. The only budget comparison in the gate is `medium-risk task vs. budget == "low"`. The CLI accepts `high` as a valid `--risk-budget` value on `scan`/`ci`, but nothing in the gate logic treats it differently from `medium` — confirmed directly in dogfooding Run C (`ci --risk-budget high` on a `schemas/` file blocked with the identical "not applicable in V1" message `medium` would produce).
- **Discovered by:** Dogfooding-010, Run C
- **Resolution:** Issue #254 — the CLI (`main.py` scan/ci validation and help text, `commands/ci.py::execute()`) now rejects `"high"` as a `--risk-budget` value; only `low`/`medium` are accepted. `scan.py` and `ci.py`'s duplicated 3-branch limit mappings collapsed to 2 branches. `RunMetadata.risk_budget`'s `Literal["low", "medium", "high"]` deliberately left unchanged (documented via comment in `schemas/artifacts.py`) so `plan`/`preview`/`apply` can still read a `run.json` persisted by a pre-fix `scan --risk-budget high` without a raw `pydantic.ValidationError`. `check_plan_gate`'s logic/message unchanged — it gates on `task.risk_level`, a separate concept from `risk_budget`.

### ✅ [2026-07-17] Issue #254 — `scan.execute()` silently accepts any unrecognized `risk_budget` value, unlike `ci.execute()` (RESOLVED in #269)

- **File:** `src/orchestrator/commands/scan.py:34-38,156-166` (`execute()`) vs. `src/orchestrator/commands/ci.py:56-59` (`execute()`)
- **Debt:** `ci.execute()` raises `ValueError` for any `risk_budget` not in `("low", "medium")`. `scan.execute()` has no equivalent guard — it trusts the caller (`main.py`'s CLI layer validates before calling it) and its own `if risk_budget == "low": ... else: ...` mapping silently treats *any* unrecognized string (not just `"high"` — also typos like `"hgih"`) as `"medium"`. Pre-existing behavior, not introduced by #254 (the `else` branch already accepted any non-`low`/`medium` string before #254's collapse); #254 only removed the dedicated-but-useless `"high"` branch, it didn't add or remove this silent-fallback property.
- **Discovered by:** Adversarial review during #254 implementation planning.
- **Why deferred (originally):** Adding a validation guard to `scan.execute()` symmetric to `ci.execute()`'s is a design decision about error-handling philosophy for the whole module (raise vs. silently normalize), not something #254's narrow CLI-rejection scope asked for. Revisit as its own small issue if a caller other than `main.py`'s CLI ever invokes `scan.execute()` directly with untrusted input.
- **Resolution:** Issue #269 added a guard symmetric to `ci.execute()`'s (`ValueError` for any `risk_budget` not in `(None, "low", "medium")`), placed at the very top of `execute()` before any git/workspace/scanner work. Closed as deliberate preventive hardening — the original "revisit only if a caller other than `main.py`'s CLI ever invokes `scan.execute()` directly" trigger had **not** actually fired (confirmed by grep: `main.py` is still the only production caller); this was fixed anyway on explicit request, not because the deferral criterion was met. `main.py`'s existing CLI-level check is left untouched as redundant defense in depth (same pattern already used for `ci.execute()`). The guard runs before `run_id` is generated, so unlike every other failure path in this function it is not persisted to `run.json` or logged via `log_event` — accepted as out of scope for the function's docstring promise ("callers can always inspect the results of an **unsupported scan**"), since an invalid `risk_budget` is a caller-side precondition violation, not a scan-result outcome; consistent with `ci.execute()`'s own guard, which has the identical property. Two regression tests added in `tests/test_scan.py` (`test_execute_rejects_invalid_risk_budget`, `test_execute_rejects_high_risk_budget`), mirroring the existing `ci.execute()` tests in `tests/test_ci_command.py`.

### ⏳ [2026-07-17] Dogfooding 010 — Interrupted `apply` leaves the target in a state that misclassifies as `CONFLICT` on retry (PARTIALLY RESOLVED, Part 1 of 4)

- **File:** `src/orchestrator/commands/apply.py` (lifecycle classification + ALREADY_APPLIED message), `src/orchestrator/lifecycle.py`, `src/orchestrator/git.py`
- **Debt:** `apply` re-runs the full validator (`ruff` + `pytest`, ~3 minutes on this repo) after applying the patch and before committing, with no progress output during that window. An `apply` process killed mid-validator-rerun leaves the target repo on the new `patchforge/<run_id>` branch with the patch already written to the working tree but uncommitted. Retrying `apply` on that same state used to fail with `Patch lifecycle state is CONFLICT... HEAD <sha> has diverged from base commit <sha>` — even though the two SHAs printed in the error message are identical, because the classifier didn't account for "already-applied-but-uncommitted, matching the pending patch" as a distinct state.
- **Discovered by:** Dogfooding-010, Run B2 (`apply` attempt 1, client-side timeout mid-validator-rerun)
- **Resolution (Part 1 only):** Issue #258 — new `PatchLifecycleState.ALREADY_APPLIED` state, detected via reverse-check (`git apply --check --reverse`) + HEAD stability + a residue-free working-tree check (temporary Git index: `read-tree` baseline → `apply --cached` forward → untracked-file comparison → `diff-files --quiet` with `core.filemode=false`). `apply` now reports this state with a clear, accurate message instead of the misleading CONFLICT error. **Automatic resume is not yet implemented** — the user must commit or discard the working tree manually and re-run `apply`. The original single-PR implementation attempted resume execution + a dirt-snapshot feature for `--allow-dirty` in the same change; it grew too large to review safely and uncovered several real bugs in review (validator-exception silently treated as success, a broken `git stash create --include-untracked` — the flag is silently ignored by real git — untracked-file content not compared, rollback misreporting success, config loaded from the mutated tree, lock-acquisition ordering). The remaining scope (Parts 2-4: resume execution, dirt-snapshot preservation, hardening) is tracked in `docs/context/plan-issue-258-resumable-apply.md`, which also documents the confirmed bugs so they aren't lost when that work resumes.

### ✅ [2026-07-18] `apply.execute()` has a redundant HEAD-divergence check that makes the REBASEABLE branch unreachable (RESOLVED in #270)

- **File:** `src/orchestrator/commands/apply.py:146-166` (early HEAD check, pre-`classify_lifecycle`), `:239-247` (`PatchLifecycleState.REBASEABLE` branch)
- **Debt:** `apply` exits early with a "HEAD has changed" `failure.json` whenever `current_head_sha != run_metadata.base_commit`, before `classify_lifecycle` ever runs. Since `classify_lifecycle` can only return `REBASEABLE` when HEAD differs from `base_commit`, the early exit means the downstream `REBASEABLE` branch is dead code in the normal single-threaded flow (it could only fire if HEAD changed in the narrow window between the two checks — a concurrent-worker race, not the intended path). Pre-existing in `main`, not introduced by the #258 Part 1 change.
- **Discovered by:** Code review during issue #258 Part 1 (PR #259).
- **Why deferred (originally):** Fixing this means picking one of two designs (rely on `classify_lifecycle` + drop the early check, or keep the early check + drop the `REBASEABLE` branch) and is unrelated to #258's scope (ALREADY_APPLIED detection). Revisit alongside a future `apply.py` control-flow cleanup rather than folding it into an unrelated fix.
- **Resolution:** Issue #270 removed the early-exit block entirely, letting `classify_lifecycle()` dispatch HEAD-divergence through its existing REBASEABLE and CONFLICT branches (already implemented, previously reachable only via mocks). Specifically: if the patch still applies cleanly over the new HEAD, `classify_lifecycle()` returns `REBASEABLE`; if it no longer applies, it returns `CONFLICT`. `STALE` covers a distinct class of errors — missing or empty patch file and process-level failures — and is not a HEAD-divergence path. Chosen over the alternative (keep the early-exit, delete REBASEABLE as dead code) because REBASEABLE is not fully dead — it remains reachable via a genuine TOCTOU race between the early HEAD read and `classify_lifecycle`'s own HEAD read, which the repo lock (opt-in, PatchForge-workers-only) does not close; deleting it would remove a real, if narrow, safety net inconsistent with the rest of the module's WAL/lock/dirt-capture investment in concurrent-race correctness. Side effects: a HEAD-diverged abort no longer writes the old ad-hoc `failure.json` (which was already a naming inconsistency with the rest of `apply.py`'s failure reporting — see new entry below); `run_metadata.lifecycle_state`/`auto_apply_eligible`/`updated_at` now get persisted to `run.json` for this case too (previously the early-exit skipped that write entirely). Three regression tests: `test_apply_aborts_if_head_changed` (existing, updated — a HEAD-diverged patch that no longer applies now classifies CONFLICT, no `failure.json`), `test_apply_head_diverged_but_patch_still_applies_yields_rebaseable` (new, real/unmocked — proves REBASEABLE is genuinely reachable, not just via `test_v1_commands.py`'s mocked test), `test_rebaseable_with_dirty_tree_leaves_dirt_untouched` (new, parametrized over `allow_dirty`True/False — proves an abort never touches or loses uncommitted working-tree changes, and that `allow_dirty` is never even evaluated on this path).

### [2026-07-20] Issue #270 — `ci.py`'s inline apply step has its own, unaudited HEAD-divergence handling (parallel implementation, not covered by this fix)

- **File:** `src/orchestrator/commands/ci.py:505-516`
- **Debt:** `ci.py` (the headless `patchforge ci` command) reimplements its own "apply" step completely independently of `commands/apply.py` — it calls `current_head()` directly but never imports `classify_lifecycle` or `PatchLifecycleState`, and never compares `pre_apply_head` against `run_metadata.base_commit` before applying. Issue #270 only touches `commands/apply.py`, per its own scope (the discovery that seeded it named only `apply.py`) — this parallel implementation in `ci.py` was never audited for the same class of problem.
- **Discovered by:** `/adversarial` review during #270 planning.
- **Why deferred:** Out of scope for #270 (a single early-exit removal); auditing/fixing `ci.py`'s inline apply step is a separate, larger design question (whether `ci.py` should route through `classify_lifecycle` at all, given it's a synchronous single-shot pipeline with a much narrower window for HEAD to diverge between its own scan and apply steps).

### [2026-07-20] Issue #270 — CONFLICT + `dirt_stash_sha` branch can misdiagnose real HEAD-divergence as a restored-stash scenario

- **File:** `src/orchestrator/commands/apply.py:357-382` (CONFLICT branch, `dirt_stash_sha` reverse-apply special case)
- **Debt:** When `run_metadata.dirt_stash_sha` is set (from an earlier `--allow-dirty` capture in the same run) and the lifecycle state is CONFLICT, the code re-runs a reverse-apply check and, if it passes, shows a specific message: "this likely means a previous run restored your pre-existing working-tree changes... but crashed before finishing." Since #270 makes CONFLICT reachable via genuine HEAD divergence (not just the crash-recovery sub-case this message was written for), a coincidental combination — HEAD moved by an unrelated commit, patch no longer applies forward, but the reverse-apply happens to pass anyway — would show this message even though no prior restoration ever happened, pointing the user at the wrong recovery narrative. No data loss (the message is only wrong about *why*, not about the actual repo state), no test currently exercises this specific combination.
- **Discovered by:** `/adversarial` review during #270 planning.
- **Why deferred:** Fixing this properly requires this branch to also distinguish "HEAD genuinely diverged" from "sub-case 2 crash recovery" — new logic, not a one-line change, and out of scope for "remove a redundant early-exit."

### [2026-07-20] Issue #270 — `current_head_sha` (apply.py:317) is an unguarded snapshot reused later in the resume path (pre-existing, unrelated to #270)

- **File:** `src/orchestrator/commands/apply.py:317` (computed), `:537` (reused in the ALREADY_APPLIED resume-path comparison)
- **Debt:** `current_head_sha`, computed once early in `execute()`, is compared again against `wal_result.pre_apply_head` later in the resume path with no re-read of HEAD in between — a theoretical TOCTOU window if HEAD changes between the two points. Pre-existing since before #270; the resume path (lines 487-593) is untouched by #270 and is mutually exclusive with the REBASEABLE branch (`classify_lifecycle` returns one or the other, never both), so this window is neither caused nor widened by #270's fix.
- **Discovered by:** `/adversarial` review during #270 planning (flagged for completeness; explicitly not attributed to #270's changes).
- **Why deferred:** No data-loss path identified, narrow window, unrelated to #270's scope.

### [2026-07-20] Issue #270 — `apply.py`'s ad-hoc `failure.json` naming was already inconsistent with the rest of the file's failure reporting (pre-existing)

- **File:** `src/orchestrator/commands/apply.py:315-331` (remaining "Failed to resolve HEAD" `failure.json` write)
- **Debt:** The `failure.json` block removed by #270 (for "HEAD has changed") and the one remaining block that still writes to the same bare `run_dir / "failure.json"` path (for "Failed to resolve HEAD") were always inconsistent with every other failure path in `apply.py`, which uses `run_metadata.failure_artifacts` + distinctly-named files (`checkout_failure`, `apply.json`, `post_apply_failure.json`). Confirmed via repo-wide grep that `docs/product-thesis-v2.md`'s documented `failure.json` schema (failure type/stage/chained exception, `PipelineAbort`) belongs to `pipeline.py`'s separate `PipelineAbortError` mechanism, not to this ad-hoc file.
- **Discovered by:** `/adversarial` review during #270 planning.
- **Why deferred:** Migrating the remaining "Failed to resolve HEAD" block to the `run_metadata.failure_artifacts` pattern is a small but separate naming-consistency cleanup, out of scope for #270's single early-exit removal.

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
