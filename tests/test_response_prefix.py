"""Unit tests for agent.response_prefix: resolve_response_prefix."""

from joyhousebot.agent.response_prefix import resolve_response_prefix


def test_resolve_response_prefix_empty_or_whitespace() -> None:
    assert resolve_response_prefix("", {}) == ""
    assert resolve_response_prefix("  ", {}) == ""
    assert resolve_response_prefix("  \n  ", {"model": "x"}) == ""


def test_resolve_response_prefix_model_provider() -> None:
    ctx = {"model": "gpt-4", "provider": "openai"}
    assert resolve_response_prefix("[{model}] ", ctx) == "[gpt-4] "
    assert resolve_response_prefix("[{provider}] ", ctx) == "[openai] "
    assert resolve_response_prefix("[{model}] [{provider}]", ctx) == "[gpt-4] [openai]"


def test_resolve_response_prefix_identity_and_identity_name() -> None:
    ctx = {"identityName": "金融", "identity": "finance"}
    assert resolve_response_prefix("{identityName}: ", ctx) == "金融: "
    # identityName takes precedence when both present; identity used as fallback
    ctx2 = {"identity": "finance"}
    assert resolve_response_prefix("{identity}: ", ctx2) == "finance: "


def test_resolve_response_prefix_thinking_level() -> None:
    ctx = {"thinking_level": "full"}
    assert resolve_response_prefix("({thinking_level}) ", ctx) == "(full) "
    ctx_empty = {}
    assert resolve_response_prefix("({thinking_level}) ", ctx_empty) == "() "


def test_resolve_response_prefix_missing_key_empty() -> None:
    assert resolve_response_prefix("[{model}]", {}) == "[]"
    assert resolve_response_prefix("[{provider}]", {}) == "[]"


def test_resolve_response_prefix_none_value_becomes_empty() -> None:
    assert resolve_response_prefix("[{model}]", {"model": None}) == "[]"


def test_resolve_response_prefix_unknown_placeholder_left_as_is() -> None:
    # Unrecognized placeholders are not in the list, so they stay unchanged
    assert resolve_response_prefix("{unknown}", {}) == "{unknown}"
    assert resolve_response_prefix("[{model}] {foo}", {"model": "x"}) == "[x] {foo}"


def test_resolve_response_prefix_no_placeholders() -> None:
    assert resolve_response_prefix("Hello world", {"model": "x"}) == "Hello world"
