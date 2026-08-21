"""Resolve response prefix template with model, provider, identity, etc."""

from typing import Any


def resolve_response_prefix(template: str, context: dict[str, Any]) -> str:
    """
    Replace template placeholders with values from context.
    Supports {model}, {provider}, {identityName}, etc. Unrecognized placeholders are left as-is.
    """
    if not template or not template.strip():
        return ""

    result = template
    # Map placeholder name -> context key (or same)
    placeholders = ["model", "provider", "identityName", "identity", "thinking_level"]
    for key in placeholders:
        value = context.get(key, context.get(key.replace("identityName", "identity"), ""))
        if value is None:
            value = ""
        result = result.replace("{" + key + "}", str(value))
    # Replace any remaining {xxx} with empty or leave as-is; plan says "未识别变量保留原样"
    return result
