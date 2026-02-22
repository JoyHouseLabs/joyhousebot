from joyhousebot.services.identity.identity_service import get_identity_http


class _Store:
    def get_identity(self):
        return type(
            "Identity",
            (),
            {
                "identity_public_key": "pk",
                "house_id": "h1",
                "status": "ready",
                "ws_url": "wss://x",
                "server_url": "https://x",
            },
        )()


def test_get_identity_http():
    payload = get_identity_http(store=_Store())
    assert payload["ok"] is True
    assert payload["data"]["house_id"] == "h1"
    assert payload["data"]["identity_public_key"] == "pk"
    assert payload["data"]["status"] == "ready"


def test_get_identity_http_no_identity():
    class EmptyStore:
        def get_identity(self):
            return None

    payload = get_identity_http(store=EmptyStore())
    assert payload["ok"] is True
    assert payload["data"]["house_id"] is None
    assert payload["data"]["status"] == "local_only"
