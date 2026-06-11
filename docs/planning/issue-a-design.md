# Design: Issue A — Structured Contract Parsing

## Goal
Replace the fragile `_extract_json()` logic with a robust, schema-aware parsing utility that converts LLM text outputs directly into validated Pydantic models.

## Design Decisions

### 1. The Parser Signature
The parser is implemented as a generic utility:
`parse_llm_response(text: str, schema: type[T]) -> T` where `T` is bound to `pydantic.BaseModel`.

### 2. Handling Multiple JSON Objects

> **Positional extraction model:** `parse_llm_response()` is a positional extractor, not a document parser. It does not treat the input text as a JSON document and does not validate or require that the text conforms to any top-level JSON structure. It finds the first position `p` at which a syntactically complete JSON object can be extracted, regardless of the surrounding context. This is intentional: LLM outputs are arbitrary text that may contain prose, templates, code fences, and JSON objects at any depth or position.

The parser iterates positions of `{` in ascending order, invoking `json.JSONDecoder().raw_decode()` at each one.

Pseudo-implementation:
```python
idx = 0
while True:
    start = text.find('{', idx)
    if start == -1:
        raise LLMParseError(text=text)
    try:
        value, end = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        idx = start + 1
        continue
    if not isinstance(value, dict):
        raise LLMParseError(text=text)
    # First valid JSON object found
    break
```
- If `raw_decode` raises `json.JSONDecodeError` at a position → that `{` is incidental (placeholder, template, prose) → skip to the next `{`.
- If `raw_decode` returns a value that is not a `dict` → `LLMParseError`.
- If `raw_decode` returns a `dict` that fails Pydantic validation → `SchemaValidationError`.
- If `raw_decode` returns a `dict` that validates → it is returned.
- Trailing prose or subsequent JSON objects (anything after `end`) are ignored.

> **Implementation note:** The extraction MUST iterate `{` positions via `text.find('{', idx)` and use `json.JSONDecoder().raw_decode()` at each one. A `JSONDecodeError` at a position indicates that `{` is incidental — advance to the next. The first successful `raw_decode()` returning a `dict` is the final candidate. Manual brace-counting, regex, or any custom heuristic is **prohibited**.
>
> **Input size guard:** For inputs exceeding `_MAX_LLM_RESPONSE_CHARS = 500_000` characters, the parser MUST raise `LLMParseError` immediately without scanning. This is an operational resource-containment mechanism, not a complexity guarantee. The algorithm has no formal asymptotic complexity bound — it guarantees correctness of parsing, not efficiency of scanning. Callers requiring demonstrable complexity guarantees over arbitrary inputs should open a separate issue for a dedicated linear scanner. This architectural debt is acknowledged and documented.
>
> **Semantic contract:** "First JSON object" is defined as the JSON object (per RFC 8259) whose opening `{` has the smallest position `p` in the text, such that there exists a `q > p` for which `text[p:q]` is a syntactically complete JSON object. This is a property of the text and the RFC 8259 grammar, not of any parser implementation. The current implementation verifies this property using `json.JSONDecoder().raw_decode()`, which is one conforming mechanism. Any future RFC 8259-compliant implementation must produce the same result for all inputs; the AC8 test suite is the conformance oracle.

### 3. Exception Hierarchy
Custom exceptions are introduced to distinguish between parsing and validation failures:
- `LLMParseError(text: str)`: Raised when no valid JSON can be extracted (malformed, truncated, or wrong top-level type like array/primitive).
- `SchemaValidationError(text: str, schema: type)`: Raised when JSON is syntactically correct but fails Pydantic validation.
- Both inherit from `Exception` (with a `# TODO` to migrate to `PatchForgeError` in T-07).

> **`text` attribute semantics:** `SchemaValidationError.text` is the extracted JSON substring (the string that passed `raw_decode()` but failed Pydantic). `LLMParseError.text` is the full input to the parser (for diagnostic purposes when no valid JSON was found).

> **Type guard condition (AC5):** The parser MUST validate the `schema` argument via:
> ```python
> if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
>     raise TypeError(f"schema must be a BaseModel subclass, got {schema!r}")
> ```
> This rejects non-types and non-Pydantic classes. `BaseModel` itself, abstract subclasses, and concrete subclasses are all accepted — the parser does not judge caller intent.

> **Stability note:** The names, constructors (`text`, `schema`), and attributes of both exception classes follow **backward-compatible evolution only**: existing attributes will not be removed or renamed; new attributes may be added in future issues as diagnostic needs are discovered through dogfooding. T-07 only migrates the base class from `Exception` to `PatchForgeError` — an additive, non-breaking operation. Any `except LLMParseError` or `except SchemaValidationError` written during Issue A remains compatible without modification.

> **Exception chaining note:** `SchemaValidationError` MUST be raised with `raise SchemaValidationError(...) from pydantic_validation_error`. The original Pydantic `ValidationError` MUST be available via `e.__cause__`. Similarly, `LLMParseError` MUST be raised with `raise LLMParseError(...) from json_decode_error` when the failure originates from a `JSONDecodeError`. This makes all diagnostic information from the original cause accessible without expanding the public contract.

### 4. Integration
- **`architect.py`**: `call_claude()` no longer performs inline fence-stripping. The raw output is passed to `parse_llm_response`.
- **`risk.py`**: Removed from scope (already receives validated objects).
- **Architectural Invariant**: `parser.py` is the single canonical path for transforming raw LLM text into Pydantic models. Any component that receives raw LLM text MUST use `parse_llm_response()`. Inline parsing or alternative extraction outside this module requires explicit ADR approval. `risk.py` is correctly out of scope — it receives already-validated models downstream of `architect.py`.

---

## Acceptance Criteria (AC)

- **AC1:** `parse_llm_response` exists as a standalone module in `orchestrator/llm/parser.py`.
- **AC2:** Correctly parses the following formats:
  - (a) Plain JSON
  - (b) JSON fences (```json ... ```)
  - (c) JSON after prose
  - (e) JSON embedded in markdown (e.g., inside a table or list)
  - (f) JSON object with braces inside string values: `'{"summary": "Fix {user_input} parser", "files": ["a.py"]}'` → parses correctly
  - (g) Prose with placeholders before JSON: `"Use {file_path} and {issue_id}.\n\nResult:\n{\"summary\": \"...\"}"` → parses correctly, ignores `{file_path}` and `{issue_id}`
  - (h) JSON dict auxiliar before contractual object: `'Template: {"file_path": "src/foo.py"}\n\n{"summary": "...", "files": [...]}'` → `SchemaValidationError` with `text='{"file_path": "src/foo.py"}'` (intentional — LLM violated the single-response contract)

  > **Note on numbering:** Sub-case (d) was intentionally removed during adversarial refinement — its edge case was absorbed into existing items. The jump from (c) to (e) is deliberate; no case is missing.
- **AC3:** Raises `LLMParseError` (including raw text) for: malformed JSON, truncated JSON, top-level primitives (null, 42), **top-level arrays**, or empty text. *(Issue A scope: the parser extracts exclusively the first top-level JSON object (`dict`). Support for `RootModel[list[...]]` constitutes a deliberate semantic modification — not an additive extension — requiring its own issue, ACs, and an explicit behavioral change note for existing consumers.)*
- **AC4:** Raises `SchemaValidationError` (including extracted JSON and target schema) for: valid JSON object `{...}` that fails Pydantic validation against the schema.
- **AC5:** Raises `TypeError` if `schema` is not a `BaseModel` subclass. The guard condition is: `not (isinstance(schema, type) and issubclass(schema, BaseModel))`. `BaseModel` itself is accepted.
- **AC6:** `_extract_json()` and inline fence-stripping are removed from `architect.py`.
- **AC7:** `Architect.run()` invokes `parse_llm_response(raw_response, ArchitectOutput)`.
- **AC8:** Tests cover all formats in AC2, both exceptions (AC3, AC4), and empty text, using `ArchitectOutput` as the reference schema.
- **AC9:** `ruff check .` returns 0 errors; `pytest` passes all tests (207+N).
- **AC10:** The module docstring of `parser.py` documents:
  - The architectural invariant: the `orchestrator/llm/` module is the canonical location for all LLM text → Pydantic transformations in PatchForge. No component may perform LLM text parsing outside this module without explicit ADR approval. `parse_llm_response()` is the canonical entry point for the object-root case (dict-rooted JSON). Non-object-root schemas require a dedicated function in `orchestrator/llm/` under a separate issue.
  - The validation amplitude: the function guarantees the returned object is a valid instance of `T` per the provided schema. The strictness of validation is a function of the schema chosen by the caller — from permissive (`BaseModel`) to strict (`ArchitectOutput`). Choosing a permissive schema is the caller's decision, not a parser error.
- **AC11:** Exception chaining is enforced: `SchemaValidationError.__cause__` preserves the Pydantic `ValidationError`; `LLMParseError.__cause__` preserves the `JSONDecodeError` when applicable. When `LLMParseError` originates from absence of `{` or from a non-dict result (no `JSONDecodeError` available), it is raised without chaining (`__cause__` is `None`). Tests must verify that `e.__cause__` is of the expected type, or `None` in the two specific non-JSONDecodeError paths.
