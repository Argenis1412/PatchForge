# Experiment: Portfólio Cross-Repo Dogfooding

**Target:** `Portf-lio/backend/` — FastAPI + Pydantic V2 + SQLModel + pytest-asyncio

**Target stack:** FastAPI 0.115 · Pydantic 2.10 · SQLModel · SQLite/PostgreSQL · Alembic · ruff · mypy · pytest (coverage ≥ 80%)

**Locations:**
```
PatchForge : C:\Users\Visitante\Desktop\Projectos de Github\PatchForge\
Target     : C:\Users\Visitante\Desktop\Projectos de Github\Portf-lio\backend\
Target venv: C:\Users\Visitante\Desktop\Projectos de Github\Portf-lio\backend\.venv\
Git root   : C:\Users\Visitante\Desktop\Projectos de Github\Portf-lio\  (backend/ is subdir)
```

## Experiment Invariants

1. `run.json` is the only context — no WorkerContext.
2. `apply.json` WAL writes directly to FS — never to ArtifactStore.
3. `run_id` ↔ patch bijection — each `run_id` produces exactly one patch.
4. Only touch acceptance-criteria files — never `core/`, `adapters/sql_repository.py`, Alembic migrations, `requirements.txt`, `.env`.
5. QA gates: `ruff check app/` → 0 errors, `pytest tests/ -q --cov-fail-under=80` → passes.
6. Labels are cosmetic — label failure never blocks the pipeline.

## Preparation

```powershell
# Create orchestrator.json in target root
$orchestrator = @'
{
  "target_path": "C:\\Users\\Visitante\\Desktop\\Projectos de Github\\Portf-lio\\backend",
  "python_path": "C:\\Users\\Visitante\\Desktop\\Projectos de Github\\Portf-lio\\backend\\.venv\\Scripts\\python.exe",
  "pytest_path": "C:\\Users\\Visitante\\Desktop\\Projectos de Github\\Portf-lio\\backend\\.venv\\Scripts\\pytest.exe",
  "ruff_path": "C:\\Users\\Visitante\\Desktop\\Projectos de Github\\Portf-lio\\backend\\.venv\\Scripts\\ruff.exe",
  "test_command": "pytest tests/ -q --cov-fail-under=80",
  "lint_command": "ruff check app/"
}
'@
Set-Content "..\Portf-lio\backend\orchestrator.json" $orchestrator -Encoding UTF8
```

## Pipeline

```powershell
cd "C:\Users\Visitante\Desktop\Projectos de Github\PatchForge"
.venv\Scripts\activate
$env:PYTHONPATH = "src"

# 1. Verify target is clean
cd "..\Portf-lio\backend"; git status; cd "$OLDPWD"

# 2. Scan (risk-budget medium for ~9k LOC)
# NOTE: inject target .venv into PATH so ruff/pytest are found
$targetVenv = "C:\Users\Visitante\Desktop\Projectos de Github\Portf-lio\backend\.venv\Scripts"
$env:Path = "$targetVenv;$env:Path"
python -m orchestrator.main scan "..\Portf-lio\backend" --risk-budget medium
# → Note the RUN_ID

# 3. Plan
python -m orchestrator.main plan <RUN_ID> --issue-file "docs\experiments\portfolo-001.md"

# 4. Preview (use --force-provider gemini for best quality/cost balance)
python -m orchestrator.main preview <RUN_ID> --force-provider gemini

# 5. Apply
python -m orchestrator.main apply <RUN_ID> --allow-dirty
```

## Post-Apply Verification

```powershell
cd "..\Portf-lio\backend"
git diff --stat                    # Only AC files
ruff check app/                    # 0 errors
pytest tests/ -q --cov-fail-under=80  # Coverage ≥ 80%
```

## Acceptance Criteria (Post-Experiment)

- [ ] `ruff check app/` → 0 errors
- [ ] `pytest tests/ -q --cov-fail-under=80` → passes
- [ ] Only AC files modified (`git diff --stat`)
- [ ] No changes to `requirements.txt`, `.env`, `alembic/versions/`, `app/core/`
- [ ] `git diff` semantically correct (human review)

## Experiment Results (2026-07-01)

**Issue:** Standardize entity type hints (`dict` → `dict[str, str]`) + add docstring to philosophy.py

### Pipeline Walkthrough

| Step | Result | Detail |
|------|--------|--------|
| `scan` | ✅ | V1 supported: yes, 39 hotspots. Required manual `.venv/Scripts` PATH injection |
| `plan` | ✅→⚠️ | 1st attempt: agent added T1 `pyproject.toml` (high-risk, blocked). Fixed by tightening issue.md |
| `preview` (openrouter/free) | ❌ | Formation.py replaced with "User Safety: safe" — hallucination |
| `preview` (gemini 2.5 flash) | ✅→❌ | Patches semantically correct but: trailing whitespace, docstring example truncated, extra blank lines |
| `apply` (manual) | ✅ | 3 files, 10 insertions, 2 deletions |
| `ruff check` | ✅ | All checks passed |
| `ruff format --check` | ✅ | 6 files already formatted |
| `pytest` | ✅ | 136 passed, 1 error (chaos_e2e pre-existing: network) |

### Lessons for PatchForge

1. **Scanner tool-path limitation:** `shutil.which("ruff")` does not find tools in the target's `.venv`. Needs config for tool paths or automatic venv PATH injection.
2. **Free-tier LLM unfit for diff generation:** OpenRouter/free generated hallucinated content. Gemini 2.5 Flash was usable but had formatting defects.
3. **Patch sanitization needed:** The executor should strip trailing whitespace and verify diffs have no unintended context changes before validation.
4. **Risk gate infra too aggressive:** `pyproject.toml` classified as infra → high-risk even when T1 merely lists it in `files_to_modify` without touching it.
5. **Default workspace at `~/.cache/patchforge/`:**
   ```
   C:\Users\Visitante\.cache\patchforge\workspaces\d3420453496d\
   runs\run_20260701_235608_d8fe44\
   ```
   Hash derived from target path. Contains: findings.json, plan.json, patch.diff, validation.json, run.json, events.jsonl, staging/.
6. **Practical provider chain:** Claude (premium) → Gemini (good) → OpenRouter (free). Locally, Gemini offered the best quality/cost balance for experiments.

### Actual Cost

- Plan (Architect): \$0.02139 (Claude Sonnet 4-6, 741 in / 1274 out tokens)
- Preview × 3: ~\$0.00 (Gemini + OpenRouter on free tiers; Claude failed)
- **Total: ~\$0.02** for 3 patches across 3 files

## Known Risks

| Risk | Mitigation |
|------|-----------|
| Git root is Portf-lio/ (not backend/) — `git diff` includes frontend/ | Run `git diff` from backend/ |
| No `[build-system]` in pyproject.toml | Does not affect PatchForge — only runs ruff+pytest |
| `pytest-asyncio` + `asyncio_mode = auto` may have edge cases | Do not modify pytest config; if it fails, debug manually |
| `--cov-fail-under=80` in post-apply validation may fail on refactor | Ensure new/modified tests cover the changed code |
| Scanner does not detect tools in target `.venv` | Inject `.venv/Scripts` into PATH before `scan` |
| LLM generates trailing whitespace in diffs | Post-process: `sed 's/[[:space:]]*$//'` on patch before `git apply` |
