"""Provider-neutral Tool contract shared by Core and extensions."""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Executable Tool interface registered through a pinned Capability."""

    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        pass

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """
        Execute the tool with given parameters.

        Args:
            **kwargs: Tool-specific parameters.

        Returns:
            String result of the tool execution.
        """
        pass

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """Validate tool parameters against JSON schema. Returns error list (empty if valid)."""
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return self._validate(params, {**schema, "type": "object"}, "")

    @classmethod
    def _matches_type(cls, value: Any, type_name: str) -> bool:
        if type_name == "null":
            return value is None
        expected = cls._TYPE_MAP.get(type_name)
        if expected is None:
            return True
        if type_name in {"integer", "number"} and isinstance(value, bool):
            return False
        return isinstance(value, expected)

    def _validate(self, val: Any, schema: dict[str, Any], path: str) -> list[str]:
        declared_type, label = schema.get("type"), path or "parameter"
        type_names = (
            [str(item) for item in declared_type]
            if isinstance(declared_type, list)
            else [str(declared_type)]
            if declared_type is not None
            else []
        )
        if type_names and not any(self._matches_type(val, item) for item in type_names):
            expected = " or ".join(type_names)
            return [f"{label} should be {expected}"]
        if val is None:
            return []

        matched_types = {
            item for item in type_names if self._matches_type(val, item)
        }

        errors = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if matched_types & {"integer", "number"}:
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if "string" in matched_types:
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if "object" in matched_types:
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {path + '.' + k if path else k}")
            for k, v in val.items():
                if k in props:
                    errors.extend(self._validate(v, props[k], path + "." + k if path else k))
        if "array" in matched_types:
            if "minItems" in schema and len(val) < schema["minItems"]:
                errors.append(f"{label} must contain at least {schema['minItems']} items")
            if "maxItems" in schema and len(val) > schema["maxItems"]:
                errors.append(f"{label} must contain at most {schema['maxItems']} items")
            if "items" in schema:
                for i, item in enumerate(val):
                    errors.extend(
                        self._validate(
                            item,
                            schema["items"],
                            f"{path}[{i}]" if path else f"[{i}]",
                        )
                    )
        return errors

    def to_schema(self) -> dict[str, Any]:
        """Convert tool to OpenAI function schema format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
