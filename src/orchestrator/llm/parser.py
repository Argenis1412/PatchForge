"""
Canonical module for LLM text -> Pydantic transformations in PatchForge.

Architectural invariant
----------------------
The ``orchestrator/llm/`` module is the canonical location for all
LLM text -> Pydantic transformations in PatchForge.  No component may
perform LLM text parsing outside this module without explicit ADR
approval.  ``parse_llm_response()`` is the canonical entry point for
the object-root case (dict-rooted JSON).  Non-object-root schemas
require a dedicated function in ``orchestrator/llm/`` under a separate
issue.

Validation amplitude
--------------------
The function guarantees the returned object is a valid instance of
*T* per the provided schema.  The strictness of validation is a
function of the schema chosen by the caller -- from permissive
(``BaseModel``) to strict (``ArchitectOutput``).  Choosing a permissive
schema is the caller's decision, not a parser error.

Input size guard: inputs exceeding ``_MAX_LLM_RESPONSE_CHARS`` (500 000 chars)
raise ``LLMParseError`` immediately. This is a resource-containment mechanism,
not a complexity guarantee.
"""

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError


class LLMParseError(Exception):
    def __init__(self, *, text: str) -> None:
        self.text = text
        super().__init__(f"No valid JSON object found in LLM response ({len(text)} chars)")


class SchemaValidationError(Exception):
    def __init__(self, *, text: str, schema: type) -> None:
        self.text = text
        self.schema = schema
        super().__init__(f"Extracted JSON failed validation against {schema.__name__}")


T = TypeVar("T", bound=BaseModel)

_MAX_LLM_RESPONSE_CHARS = 500_000


def parse_llm_response(text: str, schema: type[T]) -> T:
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise TypeError(f"schema must be a BaseModel subclass, got {schema!r}")

    if len(text) > _MAX_LLM_RESPONSE_CHARS:
        raise LLMParseError(text=text)

    decoder = json.JSONDecoder()
    idx = 0
    last_decode_error: json.JSONDecodeError | None = None

    while True:
        start = text.find("{", idx)
        if start == -1:
            if last_decode_error is not None:
                raise LLMParseError(text=text) from last_decode_error
            raise LLMParseError(text=text)

        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError as e:
            last_decode_error = e
            idx = start + 1
            continue
        last_decode_error = None

        if not isinstance(value, dict):
            raise LLMParseError(text=text)

        try:
            return schema.model_validate(value)
        except ValidationError as e:
            raise SchemaValidationError(text=json.dumps(value), schema=schema) from e
