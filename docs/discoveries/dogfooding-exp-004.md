# Dogfooding Experiment 004 — Discoveries

> Date: 2026-06-25 | Branch: `feat/exp-004-dogfooding-format-duration`
> Target: Add `format_duration()` to `src/orchestrator/observability/events.py`

---

## 1. Incomplete clone

- **Symptom:** `scan` detected files, but `plan`/`apply` produced empty patches because the executor could not find target files.
- **Root cause:** `Clon_PatchForge` (created manually by copying files) was missing ~35 files: `src/orchestrator/storage/`, `src/orchestrator/commands/`, `src/orchestrator/integrations/`, and agent submodules. It was not a clean `git clone`.
- **Lesson:** Always use `git clone` directly. Do not replicate with manual copies.

---

## 2. Groq Llama produces corrupted output

- **Symptom:** The executor returned code wrapped in ```markdown fences and truncated mid-file.
- **Root cause:** Despite explicit prompt instructions ("no markdown, no code fences, return raw code only"), Groq Llama 3.3-70b generates them anyway. `_strip_markdown()` in `providers.py` cannot recover content when fences + truncation are present.
- **Lesson:** Do not use Groq for code-gen. Force Claude via `risk_level="high"` on code modification tasks.

---

## 3. Provider fallback chain is silent

- **Symptom:** `applied_count=0` with no visible error message to the user.
- **Root cause:** The provider chain (Gemini → Groq → Claude) iterates silently. When all providers fail, no information is shown about which providers were tried or why they failed.
- **Lesson:** Log each provider failure with name + reason. Display a summary to the user.
- **Fix (#145):** `ProviderChainResult` dataclass now accumulates per-provider failures with names and reasons. Rich error panel in `preview.py` shows failed tasks and their `change.error` directly in the terminal.

---

## 4. `test_github.py` blocks pytest collection

- **Symptom:** `pytest` hangs until timeout (120s) without executing any tests.
- **Root cause:** `tests/test_github.py` imports the `github` PyPI package. When not installed, the import raises `ModuleNotFoundError`, but pytest collection does not handle it promptly.
- **Lesson:** Add `--ignore=tests\\test_github.py` to the `test_command` in `orchestrator.json`. Consider conditional skip with `pytest.importorskip`.
- **Fix (#145):** `pytest.importorskip("github")` added at module level. Collection skips in <1s when `PyGithub` is absent.

---

## 5. Workspace hash inconsistent between commands

- **Symptom:** `scan` and `plan` computed different workspace hashes from the same directory.
- **Root cause:** `_workspace_hash()` uses `Path(root_path).resolve()`, which already normalizes trailing slashes and symlinks. The actual divergence came from different resolved repo roots — e.g. when commands are run from different working directories, `resolve_git_root()` may return different paths.
- **Lesson:** Use explicit `--workspace` on every command to guarantee consistency, or ensure all commands are run from the same working directory.

---

## 6. `risk_level` determines provider quality

- **Symptom:** Type-hint tasks were evaluated as `risk_level="low"`, activating Gemini/Groq instead of Claude. Result: corrupted patches.
- **Root cause:** The LLM assigns `risk_level` during planning; tasks perceived as trivial get cheap but unreliable providers for code-gen.
- **Lesson:** For code modification tasks, the user should be able to force `risk_level="high"` from the CLI. Consider defaulting to "high" for code-gen.

---

## 7. HEAD mismatch after manual commits on the clone

- **Symptom:** `apply` fails because the base commit does not match the target repo's HEAD.
- **Root cause:** `orchestrator.json` was added to the clone and committed manually before running the full pipeline. The pipeline expected the original HEAD.
- **Lesson:** The pipeline must run end-to-end without intermediate commits on the target. If configuration is needed, do it before starting.

---

## 8. Validator timeout caused by failed pytest import

- **Symptom:** The validator times out at 120s with no clear message about the cause.
- **Root cause:** `test_github.py` causes pytest to take 120s during collection (due to internal import timeout), and the validator waits that long before reporting failure.
- **Lesson:** Validate pytest collection time separately from execution time. Add a specific timeout for collection.
