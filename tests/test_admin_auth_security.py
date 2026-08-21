from __future__ import annotations

import base64
import time

from fastapi.testclient import TestClient

from joyhousebot.api.app import create_app
from joyhousebot.api.routers import auth as auth_router
from joyhousebot.bootstrap.container import build_api_container
from joyhousebot.config.schema import Config
from joyhousebot.security.admin_auth import (
    _hotp,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    matching_totp_counter,
    recovery_code_digest,
    verify_password,
)
from tests.support.postgres_store import PostgresTestStore


def test_password_hash_and_totp_primitives() -> None:
    encoded = hash_password("correct-horse-battery-staple")
    assert encoded.startswith("scrypt$")
    assert verify_password("correct-horse-battery-staple", encoded)
    assert not verify_password("incorrect-password", encoded)

    secret = generate_totp_secret()
    counter = int(time.time() // 30)
    code = _hotp(secret, counter)
    assert matching_totp_counter(secret, code) == counter

    key = b"k" * 32
    ciphertext = encrypt_totp_secret(secret, key)
    assert secret not in ciphertext
    assert decrypt_totp_secret(ciphertext, key) == secret

    codes = generate_recovery_codes()
    assert len(codes) == len(set(codes)) == 8
    assert recovery_code_digest(codes[0]) == recovery_code_digest(codes[0].lower())


def test_admin_password_session_and_totp_flow(tmp_path, monkeypatch) -> None:
    bootstrap_password = "joyhousebot-bootstrap-password"
    new_password = "joyhousebot-secure-password-2026"
    monkeypatch.setenv("JOYHOUSEBOT_BOOTSTRAP_ADMIN_USER", "platform-admin")
    monkeypatch.setenv("JOYHOUSEBOT_BOOTSTRAP_ADMIN_PASSWORD", bootstrap_password)
    monkeypatch.setenv(
        "JOYHOUSEBOT_AUTH_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"a" * 32).decode("ascii"),
    )
    fixed_recovery_codes = [
        "AB23-CD45-EF67",
        "GH23-JK45-MN67",
        "PQ23-RS45-TU67",
        "VW23-XY45-ZA67",
        "BC24-DE46-FG68",
        "HJ24-KM46-NP68",
        "QR24-ST46-UV68",
        "WX24-YZ46-AB68",
    ]
    monkeypatch.setattr(
        auth_router,
        "generate_recovery_codes",
        lambda: fixed_recovery_codes,
    )
    store = PostgresTestStore(tmp_path / "admin-auth.db")
    app = create_app(build_api_container(config=Config(), store=store))

    with TestClient(app) as client:
        login = client.post(
            "/control/v1/auth/login",
            json={"user_id": "platform-admin", "password": bootstrap_password},
        )
        assert login.status_code == 200
        first = login.json()
        assert first["status"] == "authenticated"
        assert first["must_change_password"] is True
        first_headers = {"Authorization": f"Bearer {first['token']}"}
        assert client.get("/control/v1/me", headers=first_headers).status_code == 403
        assert client.get("/control/v1/auth/status", headers=first_headers).status_code == 200

        changed = client.post(
            "/control/v1/auth/password",
            headers=first_headers,
            json={"current_password": bootstrap_password, "new_password": new_password},
        )
        assert changed.status_code == 200
        current = changed.json()
        assert current["status"] == "authenticated"
        headers = {"Authorization": f"Bearer {current['token']}"}
        identity = client.get("/control/v1/me", headers=headers)
        assert identity.status_code == 200
        assert identity.json()["is_admin"] is True

        delegated_headers = {
            **headers,
            "X-Impersonate-User-ID": "personal-space-a",
            "X-Impersonation-Reason": "Investigate reported personal-space run",
        }
        delegated_identity = client.get("/control/v1/me", headers=delegated_headers)
        assert delegated_identity.status_code == 200
        assert delegated_identity.json()["user_id"] == "personal-space-a"
        assert delegated_identity.json()["actor_user_id"] == "platform-admin"
        assert delegated_identity.json()["impersonating"] is True

        delegated_run = client.post(
            "/control/v1/runs",
            headers=delegated_headers,
            json={
                "execution": {"mode": "agent", "agent_id": "default"},
                "input": {"content": "remember this for the selected personal space"},
            },
        )
        assert delegated_run.status_code == 202
        assert delegated_run.json()["user_id"] == "personal-space-a"

        # Authentication and control-plane routes always stay bound to the
        # authenticated administrator even while a personal user_id is active.
        delegated_auth_status = client.get("/control/v1/auth/status", headers=delegated_headers)
        assert delegated_auth_status.status_code == 200
        assert delegated_auth_status.json()["user_id"] == "platform-admin"

        malformed_target = client.get(
            "/control/v1/me",
            headers={**headers, "X-Impersonate-User-ID": "contains whitespace"},
        )
        assert malformed_target.status_code == 400

        prepared = client.post("/control/v1/auth/totp/setup", headers=headers)
        assert prepared.status_code == 200
        secret = prepared.json()["secret"]
        code = _hotp(secret, int(time.time() // 30))
        confirmed = client.post(
            "/control/v1/auth/totp/confirm",
            headers=headers,
            json={"code": code},
        )
        assert confirmed.status_code == 200
        recovery_codes = confirmed.json()["recovery_codes"]
        assert len(recovery_codes) == 8

        assert client.post("/control/v1/auth/logout", headers=headers).status_code == 200
        second_login = client.post(
            "/control/v1/auth/login",
            json={"user_id": "platform-admin", "password": new_password},
        )
        assert second_login.status_code == 200
        challenge = second_login.json()
        assert challenge["status"] == "mfa_required"
        verified = client.post(
            "/control/v1/auth/mfa/verify",
            json={
                "challenge_token": challenge["challenge_token"],
                "code": recovery_codes[0],
            },
        )
        assert verified.status_code == 200
        mfa_headers = {"Authorization": f"Bearer {verified.json()['token']}"}
        auth_status = client.get("/control/v1/auth/status", headers=mfa_headers).json()
        assert auth_status["totp_enabled"] is True
        assert auth_status["recovery_codes_remaining"] == 7

        disabled = client.post(
            "/control/v1/auth/totp/disable",
            headers=mfa_headers,
            json={"password": new_password, "code": recovery_codes[1]},
        )
        assert disabled.status_code == 200
        assert disabled.json() == {"enabled": False}

        final_login = client.post(
            "/control/v1/auth/login",
            json={"user_id": "platform-admin", "password": new_password},
        )
        assert final_login.status_code == 200
        assert final_login.json()["status"] == "authenticated"


def test_admin_session_requires_explicit_impersonation_permission(tmp_path) -> None:
    password = "limited-admin-password-2026"
    store = PostgresTestStore(tmp_path / "admin-impersonation-permission.db")
    store.upsert_platform_admin(
        user_id="root-admin",
        permissions=["*"],
        actor_id="test",
    )
    store.upsert_platform_admin(
        user_id="limited-admin",
        role="viewer",
        permissions=["platform.read"],
        actor_id="test",
    )
    store.set_admin_password(
        user_id="limited-admin",
        password_hash=hash_password(password),
        must_change_password=False,
        actor_id="test",
    )
    app = create_app(build_api_container(config=Config(), store=store))

    with TestClient(app) as client:
        login = client.post(
            "/control/v1/auth/login",
            json={"user_id": "limited-admin", "password": password},
        )
        assert login.status_code == 200
        headers = {
            "Authorization": f"Bearer {login.json()['token']}",
            "X-Impersonate-User-ID": "personal-space-b",
        }
        denied = client.get("/control/v1/me", headers=headers)
        assert denied.status_code == 403
        assert denied.json()["detail"] == "user impersonation permission required"

        # The same header cannot redirect password/MFA management to another user.
        own_auth_status = client.get("/control/v1/auth/status", headers=headers)
        assert own_auth_status.status_code == 200
        assert own_auth_status.json()["user_id"] == "limited-admin"
