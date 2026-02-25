"""Tests for joyhousebot.utils.exceptions module."""

from __future__ import annotations

import asyncio

import pytest

from joyhousebot.utils.exceptions import (
    JoyhouseBotError,
    ValidationError,
    NotFoundError,
    PermissionError,
    TimeoutError,
    RateLimitError,
    LLMError,
    ToolError,
    ChannelError,
    PluginError,
    ErrorCategory,
    sanitize_error_message,
    classify_exception,
    format_tool_error,
    safe_execute,
)


class TestExceptionClasses:
    """Test custom exception classes."""

    def test_joyhousebot_error_to_dict(self) -> None:
        exc = JoyhouseBotError("test message", code="TEST_CODE")
        result = exc.to_dict()
        assert result == {
            "error": "TEST_CODE",
            "message": "test message",
            "category": ErrorCategory.FATAL.value,
            "details": {},
        }

    def test_validation_error_with_field(self) -> None:
        exc = ValidationError("Invalid input", field="username")
        assert exc.code == "VALIDATION_ERROR"
        assert exc.category == ErrorCategory.VALIDATION
        assert exc.details == {"field": "username"}

    def test_not_found_error(self) -> None:
        exc = NotFoundError("User", "123")
        assert exc.code == "NOT_FOUND"
        assert exc.category == ErrorCategory.NOT_FOUND
        assert "User not found: 123" in str(exc)

    def test_permission_error(self) -> None:
        exc = PermissionError("Access denied", resource="/etc/passwd")
        assert exc.code == "PERMISSION_DENIED"
        assert exc.category == ErrorCategory.PERMISSION

    def test_timeout_error(self) -> None:
        exc = TimeoutError("fetch_data", 30.0)
        assert exc.code == "TIMEOUT"
        assert exc.category == ErrorCategory.TIMEOUT
        assert "30.0s" in exc.message

    def test_rate_limit_error(self) -> None:
        exc = RateLimitError("OpenAI", retry_after=60.0)
        assert exc.code == "RATE_LIMIT"
        assert exc.category == ErrorCategory.RATE_LIMIT
        assert "retry after 60.0s" in exc.message

    def test_rate_limit_error_without_retry_after(self) -> None:
        exc = RateLimitError("OpenAI")
        assert exc.code == "RATE_LIMIT"
        assert "retry after" not in exc.message

    def test_llm_error_retryable(self) -> None:
        exc = LLMError("Rate limit exceeded", provider="OpenAI", model="gpt-4", is_retryable=True)
        assert exc.code == "LLM_ERROR"
        assert exc.category == ErrorCategory.RETRYABLE

    def test_llm_error_non_retryable(self) -> None:
        exc = LLMError("Invalid API key", provider="Anthropic", is_retryable=False)
        assert exc.code == "LLM_ERROR"
        assert exc.category == ErrorCategory.FATAL

    def test_tool_error_recoverable(self) -> None:
        exc = ToolError("read_file", "File locked", is_recoverable=True)
        assert exc.code == "TOOL_ERROR"
        assert exc.category == ErrorCategory.RECOVERABLE

    def test_tool_error_fatal(self) -> None:
        exc = ToolError("exec", "Command not found", is_recoverable=False)
        assert exc.code == "TOOL_ERROR"
        assert exc.category == ErrorCategory.FATAL

    def test_channel_error_retryable(self) -> None:
        exc = ChannelError("telegram", "Connection lost", is_retryable=True)
        assert exc.code == "CHANNEL_ERROR"
        assert exc.category == ErrorCategory.RETRYABLE

    def test_plugin_error_retryable(self) -> None:
        exc = PluginError("my-plugin", "Timeout", is_retryable=True)
        assert exc.code == "PLUGIN_ERROR"
        assert exc.category == ErrorCategory.RETRYABLE


class TestSanitizeErrorMessage:
    """Test sanitize_error_message function."""

    def test_no_sensitive_info(self) -> None:
        msg = "Operation failed"
        assert sanitize_error_message(msg) == "Operation failed"

    def test_sanitize_api_key(self) -> None:
        msg = "API key: sk-1234567890abcdefghijklmnop"
        result = sanitize_error_message(msg)
        assert "sk-1234567890" not in result
        assert "[REDACTED]" in result

    def test_sanitize_token(self) -> None:
        msg = "Bearer token: xoxb-1234567890-abcdef"
        result = sanitize_error_message(msg)
        assert "xoxb-1234567890" not in result
        assert "[REDACTED]" in result

    def test_sanitize_password(self) -> None:
        msg = "Password: mySecret123"
        result = sanitize_error_message(msg)
        assert "mySecret123" not in result
        assert "[REDACTED]" in result

    def test_sanitize_secret(self) -> None:
        msg = "Secret: abcdefghijklmnopqrstuvwxyz123456"
        result = sanitize_error_message(msg)
        assert "abcdefghijklmnopqrstuvwxyz" not in result
        assert "[REDACTED]" in result

    def test_sanitize_long_string(self) -> None:
        msg = "Error: abcdefghijklmnopqrstuvwxyz1234567890abcdef"
        result = sanitize_error_message(msg)
        assert "abcdefghijklmnopqrstuvwxyz" not in result
        assert "[REDACTED]" in result

    def test_sanitize_with_custom_replacement(self) -> None:
        msg = "API key: sk-abcdefghijklmnopqrstuvwxyz1234567890"
        result = sanitize_error_message(msg, replacement="[HIDDEN]")
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in result
        assert "[HIDDEN]" in result


class TestClassifyException:
    """Test classify_exception function."""

    def test_classify_file_not_found(self) -> None:
        exc = FileNotFoundError("missing.txt")
        code, category, should_retry = classify_exception(exc)
        assert code == "FILE_NOT_FOUND"
        assert category == ErrorCategory.NOT_FOUND
        assert should_retry is False

    def test_classify_permission_error(self) -> None:
        exc = PermissionError("Permission denied")
        code, category, should_retry = classify_exception(exc)
        assert code == "PERMISSION_DENIED"
        assert category == ErrorCategory.PERMISSION
        assert should_retry is False

    def test_classify_asyncio_timeout(self) -> None:
        exc = asyncio.TimeoutError()
        code, category, should_retry = classify_exception(exc)
        assert code == "TIMEOUT"
        assert category == ErrorCategory.TIMEOUT
        assert should_retry is True

    def test_classify_connection_error(self) -> None:
        exc = ConnectionError("Connection refused")
        code, category, should_retry = classify_exception(exc)
        assert code == "CONNECTION_ERROR"
        assert category == ErrorCategory.RETRYABLE
        assert should_retry is True

    def test_classify_json_decode_error(self) -> None:
        import json
        exc = json.JSONDecodeError("Invalid JSON", "", 0)
        code, category, should_retry = classify_exception(exc)
        assert code == "JSON_PARSE_ERROR"
        assert category == ErrorCategory.VALIDATION
        assert should_retry is False

    def test_classify_value_error(self) -> None:
        exc = ValueError("Invalid value")
        code, category, should_retry = classify_exception(exc)
        assert code == "INVALID_VALUE"
        assert category == ErrorCategory.VALIDATION
        assert should_retry is False

    def test_classify_key_error(self) -> None:
        exc = KeyError("missing_key")
        code, category, should_retry = classify_exception(exc)
        assert code == "MISSING_KEY"
        assert category == ErrorCategory.VALIDATION
        assert should_retry is False

    def test_classify_type_error(self) -> None:
        exc = TypeError("Wrong type")
        code, category, should_retry = classify_exception(exc)
        assert code == "TYPE_ERROR"
        assert category == ErrorCategory.VALIDATION
        assert should_retry is False

    def test_classify_rate_limit_from_message(self) -> None:
        exc = RuntimeError("Rate limit exceeded")
        code, category, should_retry = classify_exception(exc)
        assert code == "RATE_LIMIT"
        assert category == ErrorCategory.RATE_LIMIT
        assert should_retry is True

    def test_classify_429_from_message(self) -> None:
        exc = RuntimeError("HTTP 429 Too Many Requests")
        code, category, should_retry = classify_exception(exc)
        assert code == "RATE_LIMIT"
        assert category == ErrorCategory.RATE_LIMIT
        assert should_retry is True

    def test_classify_timeout_from_message(self) -> None:
        exc = RuntimeError("Request timed out")
        code, category, should_retry = classify_exception(exc)
        assert code == "TIMEOUT"
        assert category == ErrorCategory.TIMEOUT
        assert should_retry is True

    def test_classify_not_found_from_message(self) -> None:
        exc = RuntimeError("Resource not found")
        code, category, should_retry = classify_exception(exc)
        assert code == "NOT_FOUND"
        assert category == ErrorCategory.NOT_FOUND
        assert should_retry is False

    def test_classify_permission_from_message(self) -> None:
        exc = RuntimeError("Access forbidden")
        code, category, should_retry = classify_exception(exc)
        assert code == "PERMISSION_DENIED"
        assert category == ErrorCategory.PERMISSION
        assert should_retry is False

    def test_classify_unauthorized_from_message(self) -> None:
        exc = RuntimeError("Unauthorized access")
        code, category, should_retry = classify_exception(exc)
        assert code == "UNAUTHORIZED"
        assert category == ErrorCategory.PERMISSION
        assert should_retry is False

    def test_classify_connection_from_message(self) -> None:
        exc = RuntimeError("Network connection failed")
        code, category, should_retry = classify_exception(exc)
        assert code == "CONNECTION_ERROR"
        assert category == ErrorCategory.RETRYABLE
        assert should_retry is True

    def test_classify_joyhousebot_error(self) -> None:
        exc = ToolError("test_tool", "Failed", is_recoverable=True)
        code, category, should_retry = classify_exception(exc)
        assert code == "TOOL_ERROR"
        assert category == ErrorCategory.RECOVERABLE
        assert should_retry is False  # RECOVERABLE != RETRYABLE

    def test_classify_generic_exception(self) -> None:
        exc = RuntimeError("Unknown error")
        code, category, should_retry = classify_exception(exc)
        assert code == "INTERNAL_ERROR"
        assert category == ErrorCategory.FATAL
        assert should_retry is False


class TestFormatToolError:
    """Test format_tool_error function."""

    def test_format_with_joyhousebot_error(self) -> None:
        exc = ToolError("read_file", "File not found", is_recoverable=False)
        result = format_tool_error("read_file", exc)
        assert "Error: Tool 'read_file' error: File not found" == result

    def test_format_with_generic_error(self) -> None:
        exc = RuntimeError("Something went wrong")
        result = format_tool_error("test_tool", exc)
        assert "Error: Something went wrong" == result

    def test_format_with_sensitive_info(self) -> None:
        exc = RuntimeError("API key: sk-abcdefghijklmnopqrstuvwxyz1234567890 failed")
        result = format_tool_error("test_tool", exc)
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in result
        assert "[REDACTED]" in result

    def test_format_with_details(self) -> None:
        exc = RuntimeError("Error occurred")
        result = format_tool_error("test_tool", exc, include_details=True)
        assert "INTERNAL_ERROR" in result
        assert "Error occurred" in result


class TestSafeExecute:
    """Test safe_execute function."""

    def test_safe_execute_success(self) -> None:
        def func(x: int, y: int) -> int:
            return x + y

        result = safe_execute(func, 2, 3)
        assert result == "5"

    def test_safe_execute_returns_string(self) -> None:
        def func() -> str:
            return "hello"

        result = safe_execute(func)
        assert result == "hello"

    def test_safe_execute_exception(self) -> None:
        def func() -> str:
            raise RuntimeError("Boom")

        result = safe_execute(func)
        assert "Error: Operation failed" == result

    def test_safe_execute_custom_default(self) -> None:
        def func() -> str:
            raise RuntimeError("Boom")

        result = safe_execute(func, default="Custom error")
        assert "Custom error" == result


@pytest.mark.asyncio
class TestToolErrorHandler:
    """Test tool_error_handler decorator."""

    async def test_decorator_handles_success(self) -> None:
        from joyhousebot.utils.exceptions import tool_error_handler

        @tool_error_handler("Test failed")
        async def func(x: int) -> str:
            return f"Result: {x}"

        result = await func(5)
        assert "Result: 5" == result

    async def test_decorator_handles_file_not_found(self) -> None:
        from joyhousebot.utils.exceptions import tool_error_handler

        @tool_error_handler("Read failed")
        async def func() -> str:
            raise FileNotFoundError("missing.txt")

        result = await func()
        assert "Error: File not found" == result

    async def test_decorator_handles_permission_error(self) -> None:
        from joyhousebot.utils.exceptions import tool_error_handler

        @tool_error_handler("Access failed")
        async def func() -> str:
            raise PermissionError("Permission denied")

        result = await func()
        assert "Error: Permission denied" == result

    async def test_decorator_handles_timeout(self) -> None:
        from joyhousebot.utils.exceptions import tool_error_handler

        @tool_error_handler("Operation failed")
        async def func() -> str:
            raise asyncio.TimeoutError()

        result = await func()
        assert "Error: Operation timed out" == result

    async def test_decorator_handles_joyhousebot_error(self) -> None:
        from joyhousebot.utils.exceptions import tool_error_handler

        @tool_error_handler("Test failed")
        async def func() -> str:
            raise ToolError("test_tool", "Custom error")

        result = await func()
        assert "Error: Tool 'test_tool' error: Custom error" == result

    async def test_decorator_handles_generic_exception(self) -> None:
        from joyhousebot.utils.exceptions import tool_error_handler

        @tool_error_handler("Test failed")
        async def func() -> str:
            raise RuntimeError("Boom")

        result = await func()
        assert "Error: Test failed" == result

    async def test_decorator_sync_function(self) -> None:
        from joyhousebot.utils.exceptions import tool_error_handler

        @tool_error_handler("Sync failed")
        def func() -> str:
            raise RuntimeError("Sync error")

        result = func()
        assert "Error: Sync failed" == result
