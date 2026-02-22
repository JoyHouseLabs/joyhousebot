from types import SimpleNamespace

from joyhousebot.cli.command_groups.house_command import (
    _format_control_plane_exception,
    _next_control_plane_backoff_seconds,
    _resolve_control_plane_backoff_policy,
)
from joyhousebot.control_plane import ControlPlaneClientError


def test_format_control_plane_exception_retryable() -> None:
    exc = ControlPlaneClientError(
        "temporary outage",
        code="CONTROL_PLANE_HTTP_ERROR",
        status_code=503,
        retryable=True,
    )
    level, detail = _format_control_plane_exception(exc)
    assert level == "yellow"
    assert "CONTROL_PLANE_HTTP_ERROR" in detail
    assert "status=503" in detail
    assert "retryable" in detail


def test_format_control_plane_exception_non_retryable() -> None:
    exc = ControlPlaneClientError(
        "invalid payload",
        code="CONTROL_PLANE_BAD_RESPONSE",
        status_code=400,
        retryable=False,
    )
    level, detail = _format_control_plane_exception(exc)
    assert level == "red"
    assert "CONTROL_PLANE_BAD_RESPONSE" in detail
    assert "non-retryable" in detail


def test_format_control_plane_exception_generic_fallback() -> None:
    level, detail = _format_control_plane_exception(ValueError("oops"))
    assert level == "yellow"
    assert detail == "oops"


def test_next_control_plane_backoff_seconds_uses_retryable_policy() -> None:
    retryable_exc = ControlPlaneClientError("temp", retryable=True)
    non_retryable_exc = ControlPlaneClientError("bad", retryable=False)

    retryable_delay = _next_control_plane_backoff_seconds(
        retryable_exc,
        retryable_base=2.0,
        non_retryable_base=20.0,
    )
    non_retryable_delay = _next_control_plane_backoff_seconds(
        non_retryable_exc,
        retryable_base=2.0,
        non_retryable_base=20.0,
    )
    generic_delay = _next_control_plane_backoff_seconds(
        ValueError("oops"),
        retryable_base=2.0,
        non_retryable_base=20.0,
    )

    assert retryable_delay == 2.0
    assert non_retryable_delay == 20.0
    assert generic_delay == 2.0


def test_resolve_control_plane_backoff_policy_from_config() -> None:
    config = SimpleNamespace(
        gateway=SimpleNamespace(
            control_plane_claim_retryable_backoff_seconds=1.5,
            control_plane_claim_non_retryable_backoff_seconds=12.0,
            control_plane_heartbeat_retryable_backoff_seconds=4.0,
            control_plane_heartbeat_non_retryable_backoff_seconds=25.0,
        )
    )
    policy = _resolve_control_plane_backoff_policy(config)
    assert policy["claim_retryable"] == 1.5
    assert policy["claim_non_retryable"] == 12.0
    assert policy["heartbeat_retryable"] == 4.0
    assert policy["heartbeat_non_retryable"] == 25.0

