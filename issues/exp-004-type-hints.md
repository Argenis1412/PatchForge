---
title: Add format_duration helper function to observability/events.py
severity: medium
labels: ["utility", "cleanup"]
---

## Description

Add a small pure utility function `format_duration(seconds: float) -> str` to `src/orchestrator/observability/events.py` that converts a duration in seconds to a human-readable string.

Examples:
- `format_duration(0)` → `"0s"`
- `format_duration(30)` → `"30s"`
- `format_duration(90)` → `"1m 30s"`
- `format_duration(3661)` → `"1h 1m 1s"`

Export it in `__all__` in the same file.

## Acceptance Criteria

- [x] `format_duration(seconds: float) -> str` exists in `events.py`
- [ ] Function is exported in `__all__` *(not implemented — `events.py` has no `__all__`, function is public by default)*
- [x] `ruff check .` passes with 0 errors
- [x] `ruff format --check .` is clean
- [x] `pytest` passes with the same results as before the change

## Results

| Step | Status | Notes |
|------|--------|-------|
| `scan` | ✅ | 62 hotspots |
| `plan --issue-file` | ✅ | 5 tasks, 2 files (Claude Sonnet, $0.03) |
| `preview` (executor) | ✅ | Claude produced clean diff, no markdown fences |
| `preview` (ruff) | ✅ | Passed |
| `preview` (pytest) | ✅ | Passed (after ignoring `test_github.py`) |
| `apply` | ✅ | Applied to `Clon_PatchForge_Proper` |
| `branch: feat/exp-004-dogfooding-format-duration` | ✅ | Ported to original repo |

### Bugs discovered during experiment

1. **Clone was incomplete** — old `Clon_PatchForge` was missing `storage/`, `integrations/`, and many agent submodules. Fixed by creating a proper `git clone`.
2. **Groq Llama produces corrupted output** — markdown fences + truncated file despite explicit prompt instructions. Claude works reliably.
3. **Provider fallback is silent** — when Gemini → Groq → Claude all fail, `applied_count=0` with no user-visible error message.
4. **`test_github.py` blocks pytest collection** — missing `github` PyPI package causes import error. Mitigated by `--ignore=tests/test_github.py`.
