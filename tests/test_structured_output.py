import pytest

from porthouse.runtime.structured import (
    StructuredOutputError,
    parse_structured_output,
    validate_json_schema,
)


def test_structured_output_uses_full_json_schema_constraints() -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["score", "labels"],
        "properties": {
            "score": {"type": "integer", "minimum": 1, "maximum": 5},
            "labels": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 2, "pattern": "^[a-z]+$"},
            },
        },
    }
    value = parse_structured_output(
        '```json\n{"score": 5, "labels": ["ok"]}\n```', schema
    )
    assert value == {"score": 5, "labels": ["ok"]}

    with pytest.raises(StructuredOutputError, match="structured output validation failed"):
        parse_structured_output('{"score": 9, "labels": ["X", "X"]}', schema)


def test_invalid_administrator_schema_fails_closed() -> None:
    errors = validate_json_schema("value", {"type": "unknown-type"})
    assert errors and "schema is invalid" in errors[0]
