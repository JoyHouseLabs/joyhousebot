from joyhousebot.api.http.identity_methods import get_identity_response


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


def test_get_identity_response():
    payload = get_identity_response(store=_Store())
    assert payload["ok"] is True
    assert payload["data"]["house_id"] == "h1"
