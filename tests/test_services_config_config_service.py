from joyhousebot.config.schema import Config
from joyhousebot.services.config.config_service import apply_config_update_to_domain, build_http_config_payload


class _Update:
    providers = {"openai": {"api_key": "k1"}}
    agents = {"defaults": {"model": "gpt-4o-mini"}}
    channels = {"telegram": {"enabled": True}}
    tools = {"restrict_to_workspace": True}
    gateway = {"port": 19999}
    skills = {"entries": {"demo.skill": {"enabled": True}}}
    plugins = {"entries": {"demo-plugin": {"enabled": True}}}
    wallet = None
    auth = None
    approvals = None
    browser = None
    messages = None
    commands = None
    env = None


def test_build_http_config_payload_and_apply_update():
    cfg = Config()
    apply_config_update_to_domain(config=cfg, update=_Update())
    assert cfg.providers.openai.api_key == "k1"
    assert cfg.agents.defaults.model == "gpt-4o-mini"
    assert cfg.gateway.port == 19999
    assert "demo-plugin" in cfg.plugins.entries

    payload = build_http_config_payload(config=cfg, get_wallet_payload=lambda: {"enabled": False, "address": ""})
    assert payload["ok"] is True
    assert payload["data"]["wallet"]["enabled"] is False

