# Execution Plan: Stabilizing Agent-Lab

## Goal
Stabilize the `agent-lab` system, ensuring interfaces are consistent, entrypoints are functional, and metrics are reliable.

## Phase 1: Critical Stabilization
- [x] **1.1 Fix Pipeline ↔ Agent Interfaces**: Align `Pipeline` calls with `run()` signatures in agents. 
- [x] **1.2 Fix `__main__` Entrypoints**: Rename invalid function calls (`run_scout`, `run_architect`, `validate`) to `run()` across all agent files.

## Phase 2: Quality & Testing
- [x] **2.1 Standardize Tests**: Replace legacy signature test with modular suite (test_scout.py, test_architect.py, test_executor.py, test_validator.py, test_schemas.py).
- [x] **2.2 Define Quality Gate**: Create a standard checklist/command set for pre-merge validation.

## Phase 3: Observability
- [ ] **3.1 Fix Metrics**: Ensure actual token usage is reported by `executor` and `validator` instead of estimations or zeros.
- [ ] **3.2 Uniform Persistence**: Standardize log naming for stage-based resuming.

## Phase 4: E2E and Finalization
- [ ] **4.1 Connect `main.py`**: Properly instantiate `Pipeline` in `main.py`.
- [ ] **4.2 E2E Verification**: Execute a full test run (dry-run + actual) on `targets/loja_app`.
- [ ] **4.3 Finalize ADR**: Document the finalized contract and operational policies.

---

### Step-by-Step Verification Checklist

#### 1. Contract Mismatch Verification
- [ ] **Check**: `pipeline.py` calls `run_architect(scout_output)`.
- [ ] **Actual**: `agents/architect.py` defines `run(scout_json_path: str)`.
- [ ] **Action**: Normalize to `run(scout_output_object)`.

#### 2. Entrypoint NameError Verification
- [ ] **Check**: `agents/scout.py` calls `run_scout(target)` in `__main__`.
- [ ] **Actual**: Only `run(target_path)` is defined.
- [ ] **Action**: Update `__main__` to use `run()`.

#### 3. Validator Smoke Test Verification
- [ ] **Check**: `agents/validator.py` calls `validate()` in `__main__`.
- [ ] **Actual**: Only `run()` is defined.
- [ ] **Action**: Update `__main__` to use `run()`.
