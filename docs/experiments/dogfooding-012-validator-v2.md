# Experiment: Dogfooding 012 — V2 validator operational integrity

**Issue:** #291 (child of #282)  
**Mode:** deterministic, no LLM providers

## Procedure

1. Create a temporary Git repository with V2 `ruff` and `pytest` declarations.
2. Run `patchforge doctor <target>` and inspect `support_profile: "v2"`.
3. Prepare a deterministic previewed run and invoke `apply`.
4. Verify V2 `validation.json`, candidate subject, promotion receipt, and an
   unchanged caller checkout.

## Expected evidence

- V2 uses PatchForge's inherited environment, not target `.venv`.
- Candidate-root `ruff.py` and `pytest.py` cannot shadow trusted adapters.
- Caches are external; persistent validation-root writes fail closed.

## Result

Automated regressions cover the deterministic path. Full LLM dogfooding is
deferred because provider credits are unavailable.
