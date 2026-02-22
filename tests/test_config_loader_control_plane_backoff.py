from joyhousebot.config.loader import _migrate_config
from joyhousebot.config.schema import Config


def test_migrate_control_plane_backoff_fields() -> None:
    data = {
        "gateway": {
            "controlPlane": {
                "claimBackoff": {"retryableSeconds": 2, "nonRetryableSeconds": 10},
                "heartbeatBackoff": {"retryableSeconds": 6, "nonRetryableSeconds": 40},
            }
        }
    }
    migrated = _migrate_config(data)
    gateway = migrated["gateway"]
    assert gateway["controlPlaneClaimRetryableBackoffSeconds"] == 2.0
    assert gateway["controlPlaneClaimNonRetryableBackoffSeconds"] == 10.0
    assert gateway["controlPlaneHeartbeatRetryableBackoffSeconds"] == 6.0
    assert gateway["controlPlaneHeartbeatNonRetryableBackoffSeconds"] == 40.0


def test_gateway_backoff_defaults_exist() -> None:
    cfg = Config()
    assert cfg.gateway.control_plane_claim_retryable_backoff_seconds == 3.0
    assert cfg.gateway.control_plane_claim_non_retryable_backoff_seconds == 15.0
    assert cfg.gateway.control_plane_heartbeat_retryable_backoff_seconds == 5.0
    assert cfg.gateway.control_plane_heartbeat_non_retryable_backoff_seconds == 30.0


def test_migrate_model_primary_and_fallbacks() -> None:
    data = {
        "agents": {
            "defaults": {"model": {"primary": "openai/gpt-4.1", "fallbacks": ["anthropic/claude-3-5-sonnet"]}},
            "list": [
                {
                    "id": "a1",
                    "name": "A1",
                    "model": {"primary": "openai/gpt-4o-mini", "fallbacks": ["openrouter/deepseek-chat"]},
                }
            ],
        }
    }
    migrated = _migrate_config(data)
    assert migrated["agents"]["defaults"]["model"] == "openai/gpt-4.1"
    assert migrated["agents"]["defaults"]["modelFallbacks"] == ["anthropic/claude-3-5-sonnet"]
    assert migrated["agents"]["list"][0]["model"] == "openai/gpt-4o-mini"
    assert migrated["agents"]["list"][0]["modelFallbacks"] == ["openrouter/deepseek-chat"]

