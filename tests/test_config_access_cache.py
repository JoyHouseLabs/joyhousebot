from porthouse.config import access
from porthouse.config.schema import Config


def test_get_config_uses_cache_and_force_reload(monkeypatch, tmp_path):
    calls = {"n": 0}

    def _fake_load_config(_path=None):
        calls["n"] += 1
        cfg = Config()
        cfg.gateway.max_concurrent_sessions = calls["n"]
        return cfg

    monkeypatch.setattr(access, "load_config", _fake_load_config)
    config_path = tmp_path / "config.json"

    first = access.get_config(config_path=config_path)
    second = access.get_config(config_path=config_path)
    third = access.get_config(config_path=config_path, force_reload=True)

    assert first.gateway.max_concurrent_sessions == second.gateway.max_concurrent_sessions
    assert third.gateway.max_concurrent_sessions != second.gateway.max_concurrent_sessions
    assert calls["n"] == 2
