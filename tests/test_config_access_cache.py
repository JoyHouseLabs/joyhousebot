from joyhousebot.config import access
from joyhousebot.config.schema import Config


def test_get_config_uses_cache_and_force_reload(monkeypatch):
    calls = {"n": 0}

    def _fake_load_config(_path=None):
        calls["n"] += 1
        cfg = Config()
        cfg.gateway.port = 18000 + calls["n"]
        return cfg

    monkeypatch.setattr(access, "load_config", _fake_load_config)
    access.clear_config_cache()

    first = access.get_config()
    second = access.get_config()
    third = access.get_config(force_reload=True)

    assert first.gateway.port == second.gateway.port
    assert third.gateway.port != second.gateway.port
    assert calls["n"] == 2

