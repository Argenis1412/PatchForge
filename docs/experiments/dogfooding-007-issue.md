# Issue: Add Claude to the validator's provider fallback chain

## Severity
low

## Problem

The validator agent's error summarization calls the shared provider chain with only
one provider (OpenRouter) as fallback after Gemini. If Gemini is unavailable and
OpenRouter also fails or is rate-limited, the validator degrades immediately to raw
stderr with no LLM summary.

The executor uses a three-provider chain (Gemini → OpenRouter → Claude). The
validator should match this coverage so it also has Claude available as a last-resort
LLM fallback before giving up.

## Acceptance Criteria

- [ ] The validator's shared-chain fallback call includes Claude alongside OpenRouter
  (provider order: OpenRouter first, then Claude — matching least-expensive-first policy)
- [ ] When all three providers fail, the validator still degrades gracefully: returns
  raw stderr summary with an empty model string
- [ ] The summarizer's external contract is unchanged: same input, same output type
- [ ] `ruff check .` passes with 0 errors
- [ ] `pytest` passes (existing tests must not be deleted or modified to pass)

## Scope

Limit changes to the validator agent. Do not touch the executor, the architect, the
scanner, the runner modules, or any command module. Do not modify or delete any
existing test file.
