"""Core-enforced outbound HTTP and SSRF protections for trusted extensions."""

from porthouse.runtime.http_tracking import TrackedAsyncClient
from porthouse.utils.exceptions import RateLimitError, TimeoutError, sanitize_error_message
from porthouse.utils.ssrf import (
    DEFAULT_MAX_BYTES,
    ResponseTooLargeError,
    SsrfBlockedError,
    SsrfProtectedTransport,
    TooManyRedirectsError,
    UnsupportedContentTypeError,
    fetch_url,
    fetch_url_bytes,
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
    "fetch_url_bytes",
    "sanitize_error_message",
    "validate_url",
    "validate_url_with_dns",
]
