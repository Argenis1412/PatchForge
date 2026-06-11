"""Tests for orchestrator/llm/parser.py -- AC2 through AC5, AC8, AC11."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from orchestrator.llm.parser import (
    _MAX_LLM_RESPONSE_CHARS,
    LLMParseError,
    SchemaValidationError,
    parse_llm_response,
)
from orchestrator.schemas.architect_output import ArchitectOutput

_VALID_JSON = (
    '{"validated_findings": [], "false_positives": [],'
    ' "systemic_risks": [], "implementation_plan": [], "blockers": []}'
)


# ---------------------------------------------------------------------------
# AC2 -- parsing formats
# ---------------------------------------------------------------------------


class TestAc2Parsing:
    def test_plain_json(self):
        result = parse_llm_response(_VALID_JSON, ArchitectOutput)
        assert isinstance(result, ArchitectOutput)

    def test_json_fences(self):
        raw = "```json\n" + _VALID_JSON + "\n```"
        result = parse_llm_response(raw, ArchitectOutput)
        assert isinstance(result, ArchitectOutput)

    def test_prose_before_json(self):
        raw = "Here is the plan:\n" + _VALID_JSON
        result = parse_llm_response(raw, ArchitectOutput)
        assert isinstance(result, ArchitectOutput)

    def test_json_in_markdown(self):
        raw = "- Result: " + _VALID_JSON
        result = parse_llm_response(raw, ArchitectOutput)
        assert isinstance(result, ArchitectOutput)

    def test_braces_in_string_value(self):
        raw = (
            '{"summary": "Fix {user_input} parser",'
            ' "validated_findings": [], "false_positives": [],'
            ' "systemic_risks": [], "implementation_plan": [], "blockers": []}'
        )
        result = parse_llm_response(raw, ArchitectOutput)
        assert isinstance(result, ArchitectOutput)

    def test_placeholders_before_json(self):
        raw = "Use {file_path} and {issue_id}.\n\nResult:\n" + _VALID_JSON
        result = parse_llm_response(raw, ArchitectOutput)
        assert isinstance(result, ArchitectOutput)

    def test_aux_dict_before_contractual(self):
        raw = 'Template: {"file_path": "src/foo.py"}\n\n' + _VALID_JSON
        with pytest.raises(SchemaValidationError) as exc_info:
            parse_llm_response(raw, ArchitectOutput)
        assert exc_info.value.text == '{"file_path": "src/foo.py"}'


# ---------------------------------------------------------------------------
# AC3 -- LLMParseError
# ---------------------------------------------------------------------------


class TestAc3LlmpParseError:
    def test_malformed_json(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response("{invalid json here", ArchitectOutput)
        assert exc_info.value.text == "{invalid json here"
        assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)

    def test_truncated_json(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response('{"key": "value', ArchitectOutput)
        assert exc_info.value.text == '{"key": "value'
        assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)

    def test_top_level_primitive_null(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response("null", ArchitectOutput)
        assert exc_info.value.text == "null"

    def test_top_level_primitive_number(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response("42", ArchitectOutput)
        assert exc_info.value.text == "42"

    def test_top_level_array(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response('["a", "b"]', ArchitectOutput)
        assert exc_info.value.text == '["a", "b"]'

    def test_empty_text(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response("", ArchitectOutput)
        assert exc_info.value.text == ""

    def test_input_too_large(self):
        large = "x" * (_MAX_LLM_RESPONSE_CHARS + 1)
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response(large, ArchitectOutput)
        assert exc_info.value.text is large


# ---------------------------------------------------------------------------
# AC4 -- SchemaValidationError
# ---------------------------------------------------------------------------


class TestAc4SchemaValidationError:
    def test_valid_json_invalid_schema(self):
        raw = '{"not_a_field": 123}'
        with pytest.raises(SchemaValidationError) as exc_info:
            parse_llm_response(raw, ArchitectOutput)
        assert exc_info.value.text == raw
        assert exc_info.value.schema is ArchitectOutput
        assert isinstance(exc_info.value.__cause__, ValidationError)


# ---------------------------------------------------------------------------
# AC5 -- TypeError
# ---------------------------------------------------------------------------


class TestAc5TypeError:
    def test_schema_not_a_type(self):
        with pytest.raises(TypeError, match="schema must be a BaseModel subclass"):
            parse_llm_response("{}", "not_a_class")

    def test_schema_non_pydantic_class(self):
        with pytest.raises(TypeError, match="schema must be a BaseModel subclass"):
            parse_llm_response("{}", dict)

    def test_schema_is_basemodel_itself(self):
        with pytest.raises(Exception) as exc_info:
            parse_llm_response("{}", BaseModel)
        assert "PydanticUserError" in type(exc_info.value).__name__


# ---------------------------------------------------------------------------
# AC11 -- Exception chaining
# ---------------------------------------------------------------------------


class TestAc11Chaining:
    def test_schema_validation_error_chains_validation_error(self):
        with pytest.raises(SchemaValidationError) as exc_info:
            parse_llm_response('{"not_a_field": 123}', ArchitectOutput)
        assert isinstance(exc_info.value.__cause__, ValidationError)

    def test_llm_parse_error_no_brace_has_no_cause(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response("no braces here", ArchitectOutput)
        assert exc_info.value.__cause__ is None

    def test_llm_parse_error_non_dict_has_no_cause(self):
        with pytest.raises(LLMParseError) as exc_info:
            parse_llm_response("42", ArchitectOutput)
        assert exc_info.value.__cause__ is None
