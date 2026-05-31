# ADR-0001: Architect Model Comparison

## Context
During the development phase of the `Architect` agent, two models were evaluated: Gemini Flash (used by the Scout) and Claude Sonnet (`claude-sonnet-4-6`). The Architect's goal is to receive the `Scout`'s findings (in JSON format), validate them, detect false positives, prioritize them, and generate a robust implementation plan.

A `compare_architects.py` script was executed, which sent the same input (Scout output) to both models to analyze their responses.

### Comparison Results

| Metric | Gemini Flash | Claude Sonnet (`claude-sonnet-4-6`) |
| :--- | :--- | :--- |
| **Execution Time** | 25.96s | 43.26s |
| **Tokens (In / Out)** | 720 / 1165 | 762 / 2222 |
| **Cost** | $0.00040 | $0.03562 |
| **Tasks Identified** | 5 | 6 |
| **False Positives** | 0 | 2 |

#### Key Findings
1. **Analysis Depth**: Gemini Flash provided high-level summaries of the Scout's findings, but Claude Sonnet detailed the *why* behind the risks (e.g., lack of cross-validation, implicit frontend-backend contracts, mutation of domain models by the service layer).
2. **False Positive Detection**: Claude Sonnet correctly identified 2 false positives from the Scout:
   - It noted that `recibo_models.py` is a downstream artifact of the idempotency concern in `pagamentos_api.py`, not an independent hotspot.
   - It downgraded the severity of `settings.py`, noting it is an architectural role (latent risk) and not an active defect unless it causes a known failure.
3. **Dependency and File Analysis**: Claude was able to identify additional affected files not listed by Gemini (e.g., `postgres_recibo_repository.py`).
4. **Blockers and Systemic Risks**: The blockers proposed by Claude were actionable and sequential (e.g., "T-03 must be complete before T-05", "lack of tests blocks high-risk modifications"), whereas Gemini gave more generic blockers.

## Decision
It has been decided to use **Claude Sonnet** as the primary and exclusive model for the `Architect` role.

Although the cost of Claude Sonnet is significantly higher (~88x more expensive than Gemini Flash), its reasoning capability fully justifies the investment at this critical stage of the pipeline.

## Consequences
- **Efficacy**: A much more robust planning phase is ensured. Claude prevents the Executor from working on false positives, reducing rework.
- **Cost**: The budget spent per run at this stage is increased (to ~$0.035), but an economically viable approach is maintained by using Gemini Flash for the `Scout` (massive exploration) and DeepSeek for the `Executor` in mechanical tasks.
- **Maintenance**: The architectural design role, sequential task prioritization, and complex logic validation are strictly reserved for Claude.
