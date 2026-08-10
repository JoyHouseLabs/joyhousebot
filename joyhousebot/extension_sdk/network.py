"""Core-enforced outbound HTTP and SSRF protections for trusted extensions."""

from joyhousebot.runtime.http_tracking import TrackedAsyncClient
from joyhousebot.utils.exceptions import RateLimitError, TimeoutError, sanitize_error_message
from joyhousebot.utils.ssrf import (
    DEFAULT_MAX_BYTES,
    ResponseTooLargeError,
    SsrfBlockedError,
    SsrfProtectedTransport,
    TooManyRedirectsError,
    UnsupportedContentTypeError,
    fetch_url,
    validate_url,
    validate_url_with_dns,
)

__all__ = [
    "DEFAULT_MAX_BYTES",
    "RateLimitError",
    "ResponseTooLargeError",
    "SsrfBlockedError",
    "SsrfProtectedTransport",
    "TimeoutError",
    "TooManyRedirectsError",
    "TrackedAsyncClient",
    "UnsupportedContentTypeError",
    "fetch_url",
    "sanitize_error_message",
    "validate_url",
    "validate_url_with_dns",
]
