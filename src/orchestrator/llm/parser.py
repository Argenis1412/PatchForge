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
"""

import json
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from orchestrator.exceptions import LLMParseError, SchemaValidationError

T = TypeVar("T", bound=BaseModel)

_MAX_LLM_RESPONSE_CHARS = 500_000


def parse_llm_response(text: str, schema: type[T]) -> T:
    if not (isinstance(schema, type) and issubclass(schema, BaseModel)):
        raise TypeError(f"schema must be a BaseModel subclass, got {schema!r}")

    if len(text) > _MAX_LLM_RESPONSE_CHARS:
        raise LLMParseError(text=text)

    decoder = json.JSONDecoder()
    idx = 0

    while True:
        start = text.find("{", idx)
        if start == -1:
            raise LLMParseError(text=text)

        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue

        if not isinstance(value, dict):
            raise LLMParseError(text=text)

        try:
            return schema.model_validate(value)
        except ValidationError as e:
            raise SchemaValidationError(text=text[start:end], schema=schema) from e
