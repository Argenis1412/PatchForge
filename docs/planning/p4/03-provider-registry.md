# P4 — 3. Provider Registry

> **Source of truth:** `docs/planning/roadmap.md` §P4-3 (idea 9)
> **Status:** 📐 Scoped (not yet opened as GitHub issue)
> ⚠️ This doc is an implementation guide. Verify file paths and function signatures against the codebase before coding. The roadmap is authoritative for goal, effort, and cuts.

## Context

Model constants in `src/orchestrator/agents/executor/providers.py` (`MODEL_GEMINI`, `MODEL_OPENROUTER`, `MODEL_CLAUDE`, cost constants, `_PROVIDER_CHAIN`) are hardcoded today. Enterprise users on Azure/Bedrock, or users wanting a non-free OpenRouter model, must edit source to change them. This item makes the models configurable via a `providers` section in `orchestrator.json`, with the current constants becoming defaults. Also referenced by memory as an existing project goal — see `[[project_openrouter-configurable-model]]`.

## Scope

- A `providers` config section (config file: `orchestrator.json`) allowing per-role model overrides.
- Current `providers.py` constants become fallback/default values, not the only source of truth.
- The model actually used per role is recorded in `RunMetadata` for audit (feeds item 4's manifest).

See `roadmap.md` §P4-3 for the full Goal/Impact/Cuts text.

## Non-goals / Cuts

- No new providers or custom endpoints (roadmap: "Model field only — no custom endpoints, no plugin providers").
- No multi-model cost table.
- No changes to the fallback chain logic or risk-level routing (`_PROVIDER_CHAIN` selection by risk stays as-is — only the model names inside become overridable).
- Overriding Claude records `cost_llm: null` + a warning rather than computing a wrong cost number (roadmap Cuts, verbatim).

## Open questions

- **`cost_llm` field:** confirmed absent from the codebase as of this writing (no matches in `src/`) — it does not currently exist as a field anywhere, not just on `RunMetadata`. The roadmap's "records `cost_llm: null`" cut implies this field needs to be added as part of this item, or the cut needs to be re-scoped. Resolve during Clarifier: is `cost_llm` a new field, and if so, where does it live and does it require a schema_version consideration under ADR-0004?
- Config schema location: `TargetConfig` in `src/orchestrator/schemas/config.py` (confirmed to exist at line 72) is the most likely owner of the `providers` section, but confirm during Clarifier whether `orchestrator.json` maps to `TargetConfig` directly or through another layer.

## Preconditions

None.

## Files likely to be touched

| File | Change type |
|---|---|
| `src/orchestrator/schemas/config.py` | EDIT — add `providers` section to `TargetConfig` (pending confirmation this is the right owner) |
| `src/orchestrator/agents/executor/providers.py` | EDIT — constants become fallback defaults; all internal usages read from config first |
| `src/orchestrator/schemas/artifacts.py` | EDIT — record chosen model per role on `RunMetadata` |
| Tests covering `providers.py` (exact file TBD — grep for existing provider tests) | EDIT — default path, override path, cost-attribution warning |

## Implementation steps

1. **Verify** which schema file owns `orchestrator.json` config parsing — confirm `TargetConfig` (`schemas/config.py:72`) is the entry point before adding the `providers` section there.
2. **Verify** whether `cost_llm` exists anywhere as a field today (confirmed absent from `src/` as of this scaffold) — decide during Clarifier whether adding it is in-scope for this item or a precondition.
3. **Enumerate** all import-sites of the provider constants — `MODEL_GEMINI`, `MODEL_OPENROUTER`, `MODEL_CLAUDE`, `COST_PER_1M_INPUT_CLAUDE`, `COST_PER_1M_OUTPUT_CLAUDE`, `MAX_RETRIES`, `_PROVIDER_CHAIN` (all confirmed present in `providers.py` lines 27–41, 236–238) — grep every file that imports them so the refactor surface is fully mapped before coding.
4. Extend the confirmed config schema with a `providers` section (per-role model overrides), defaulting to the current hardcoded values.
5. Update `providers.py` so the enumerated constants become fallback values — every import site now resolves through config first, falling back to the hardcoded default.
6. Record the chosen model per role in `RunMetadata` for audit (this is what item 4's manifest reads).
7. Cost-attribution edge case: overriding Claude's model records `cost_llm: null` + a warning rather than a wrong number — implement per the open question above's resolution.
8. Tests: default path (no `providers` key in config → current behavior unchanged), explicit override (config model wins), Claude-override warning path.

## Branch & commit

Branch: `⚠️ REPLACE XXX with GitHub issue number` → `feat/issue-XXX-provider-registry`
Suggested commit prefix: `feat(config): …`
Commit granularity decided at pickup — one logical change per commit.

## Acceptance criteria (placeholder)

Full ACs written in GitHub issue at pickup time (Clarifier → AC Challenger → Adversarial Reviewer flow).
Minimum bar before merge:
- Roadmap Cuts respected (no new providers, no custom endpoints, no fallback-chain-logic changes).
- QA gate green (`ruff check .` + `ruff format --check .` + `pytest`).
- Tests added for the behavioral change (per Workflow.md testing table).
- `docs/context/CONTEXT.md` "Completed" section updated in same PR.
- `docs/planning/issue-registry.md` status flipped to ✅ Completed with PR link.
