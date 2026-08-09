"""Administrator password, browser-session, and TOTP authentication API."""

from __future__ import annotations

import asyncio
import os
from functools import lru_cache

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from joyhousebot.api.dependencies import ContainerDep, PrincipalDep
from joyhousebot.security.admin_auth import (
    DEFAULT_DEVELOPMENT_ADMIN_PASSWORD,
    auth_encryption_key,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    matching_totp_counter,
    recovery_code_digest,
    totp_uri,
    validate_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["administrator-auth"])


class LoginRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class VerifyMfaRequest(BaseModel):
    challenge_token: str = Field(min_length=20, max_length=256)
    code: str = Field(min_length=6, max_length=64)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class TotpCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class DisableTotpRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=6, max_length=64)


def _production() -> bool:
    return str(os.getenv("JOYHOUSEBOT_ENVIRONMENT") or "development").strip().lower() in {
        "prod",
        "production",
    }


def _session_seconds() -> int:
    try:
        hours = int(os.getenv("JOYHOUSEBOT_ADMIN_SESSION_HOURS") or "12")
    except ValueError:
        hours = 12
    return max(1, min(168, hours)) * 3600


def _totp_key() -> bytes:
    development_password = str(
        os.getenv("JOYHOUSEBOT_DEV_ADMIN_PASSWORD") or DEFAULT_DEVELOPMENT_ADMIN_PASSWORD
    )
    try:
        return auth_encryption_key(
            production=_production(),
            development_password=development_password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@lru_cache(maxsize=1)
def _dummy_password_hash() -> str:
    # Equalize the expensive password operation for unknown users so the
    # login endpoint does not become a practical administrator enumerator.
    return hash_password("joyhousebot-invalid-login-password")


async def _issue_session(container, user_id: str, *, mfa_verified: bool) -> dict:
    record, token = await asyncio.to_thread(
        container.store.create_admin_auth_session,
        user_id,
        expires_seconds=_session_seconds(),
        mfa_verified=mfa_verified,
    )
    auth_status = await asyncio.to_thread(container.store.get_admin_auth_status, user_id)
    return {
        "status": "authenticated",
        "token": token,
        "token_type": "bearer",
        "expires_at": record["expires_at"],
        "user_id": user_id,
        "must_change_password": bool((auth_status or {}).get("must_change_password")),
        "totp_enabled": bool((auth_status or {}).get("totp_enabled")),
    }


async def _verify_active_second_factor(store, credential: dict, code: str) -> bool:
    user_id = str(credential["user_id"])
    raw_code = str(code)
    normalized_digits = "".join(character for character in raw_code if character.isdigit())
    is_totp_format = len(normalized_digits) == 6 and all(
        character.isdigit() or character in " -\t\r\n" for character in raw_code
    )
    if is_totp_format:
        ciphertext = str(credential.get("totp_secret_ciphertext") or "")
        if not ciphertext:
            return False
        try:
            secret = decrypt_totp_secret(ciphertext, _totp_key())
        except (TypeError, ValueError):
            return False
        counter = matching_totp_counter(secret, normalized_digits, window=1)
        if counter is None:
            return False
        return await asyncio.to_thread(store.accept_admin_totp_counter, user_id, counter)
    return await asyncio.to_thread(
        store.consume_admin_recovery_code,
        user_id,
        recovery_code_digest(code),
    )


def _principal_user_id(principal) -> str:
    user_id = str(principal.user_id or "").strip()
    if not user_id:
        raise HTTPException(status_code=400, detail="administrator user identity is required")
    if not principal.can("platform.read"):
        raise HTTPException(status_code=403, detail="platform administrator permission required")
    return user_id


@router.post("/login")
async def login(body: LoginRequest, container: ContainerDep):
    user_id = body.user_id.strip()
    credential = await asyncio.to_thread(container.store.get_admin_login_credential, user_id)
    password_hash = str((credential or {}).get("password_hash") or _dummy_password_hash())
    password_ok = await asyncio.to_thread(verify_password, body.password, password_hash)
    if (
        credential is None
        or not credential.get("admin_enabled")
        or credential.get("is_locked")
        or not password_ok
    ):
        if credential is not None and not credential.get("is_locked"):
            await asyncio.to_thread(container.store.record_admin_login_failure, user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials or account temporarily unavailable",
        )
    await asyncio.to_thread(container.store.record_admin_login_success, user_id)
    if credential.get("totp_enabled"):
        challenge_token, expires_at = await asyncio.to_thread(
            container.store.create_admin_auth_challenge,
            user_id,
            expires_seconds=300,
        )
        return {
            "status": "mfa_required",
            "challenge_token": challenge_token,
            "expires_at": expires_at,
            "user_id": user_id,
        }
    return await _issue_session(container, user_id, mfa_verified=False)


@router.post("/mfa/verify")
async def verify_mfa(body: VerifyMfaRequest, container: ContainerDep):
    challenge = await asyncio.to_thread(
        container.store.get_admin_auth_challenge, body.challenge_token
    )
    if challenge is None or not challenge.get("admin_enabled"):
        raise HTTPException(status_code=401, detail="invalid or expired MFA challenge")
    credential = await asyncio.to_thread(
        container.store.get_admin_login_credential, str(challenge["user_id"])
    )
    accepted = bool(
        credential
        and credential.get("totp_enabled")
        and await _verify_active_second_factor(container.store, credential, body.code)
    )
    if not accepted:
        await asyncio.to_thread(
            container.store.record_admin_auth_challenge_failure, body.challenge_token
        )
        raise HTTPException(status_code=401, detail="invalid authentication code")
    consumed = await asyncio.to_thread(
        container.store.consume_admin_auth_challenge, body.challenge_token
    )
    if not consumed:
        raise HTTPException(status_code=401, detail="invalid or expired MFA challenge")
    return await _issue_session(
        container,
        str(challenge["user_id"]),
        mfa_verified=True,
    )


@router.get("/status")
async def auth_status(principal: PrincipalDep, container: ContainerDep):
    user_id = _principal_user_id(principal)
    result = await asyncio.to_thread(container.store.get_admin_auth_status, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="administrator password is not configured")
    return {**result, "session_authenticated": principal.subject.startswith("session:")}


@router.post("/password")
async def change_password(
    body: ChangePasswordRequest,
    principal: PrincipalDep,
    container: ContainerDep,
):
    user_id = _principal_user_id(principal)
    credential = await asyncio.to_thread(container.store.get_admin_login_credential, user_id)
    if credential is None or not await asyncio.to_thread(
        verify_password, body.current_password, credential["password_hash"]
    ):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    validate_password(body.new_password)
    if await asyncio.to_thread(
        verify_password, body.new_password, credential["password_hash"]
    ):
        raise HTTPException(status_code=422, detail="new password must differ from current password")
    encoded = await asyncio.to_thread(hash_password, body.new_password)
    updated = await asyncio.to_thread(
        container.store.set_admin_password,
        user_id=user_id,
        password_hash=encoded,
        must_change_password=False,
        actor_id=principal.subject,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="administrator not found")
    # Password replacement revokes every browser session. Give the active
    # password-authenticated browser one fresh session so the UI can continue.
    if principal.subject.startswith("session:"):
        return await _issue_session(
            container,
            user_id,
            mfa_verified=bool(credential.get("totp_enabled")),
        )
    return {"status": "password_changed", "user_id": user_id}


@router.post("/totp/setup")
async def setup_totp(principal: PrincipalDep, container: ContainerDep):
    user_id = _principal_user_id(principal)
    auth_state = await asyncio.to_thread(container.store.get_admin_auth_status, user_id)
    if auth_state is None:
        raise HTTPException(status_code=404, detail="administrator password is not configured")
    if auth_state.get("must_change_password"):
        raise HTTPException(status_code=409, detail="change the bootstrap password before enabling TOTP")
    if auth_state.get("totp_enabled"):
        raise HTTPException(status_code=409, detail="TOTP is already enabled")
    secret = generate_totp_secret()
    expires_at = await asyncio.to_thread(
        container.store.set_admin_totp_pending,
        user_id,
        secret_ciphertext=encrypt_totp_secret(secret, _totp_key()),
        expires_seconds=600,
    )
    if not expires_at:
        raise HTTPException(status_code=409, detail="unable to prepare TOTP enrollment")
    return {
        "secret": secret,
        "otpauth_uri": totp_uri(secret=secret, user_id=user_id),
        "expires_at": expires_at,
        "algorithm": "SHA1",
        "digits": 6,
        "period": 30,
    }


@router.post("/totp/confirm")
async def confirm_totp(
    body: TotpCodeRequest,
    principal: PrincipalDep,
    container: ContainerDep,
):
    user_id = _principal_user_id(principal)
    credential = await asyncio.to_thread(container.store.get_admin_login_credential, user_id)
    ciphertext = str((credential or {}).get("totp_pending_secret_ciphertext") or "")
    if not ciphertext:
        raise HTTPException(status_code=409, detail="no pending TOTP enrollment")
    try:
        secret = decrypt_totp_secret(ciphertext, _totp_key())
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="pending TOTP enrollment is invalid") from exc
    counter = matching_totp_counter(secret, body.code, window=1)
    if counter is None:
        raise HTTPException(status_code=401, detail="invalid authentication code")
    recovery_codes = generate_recovery_codes()
    activated = await asyncio.to_thread(
        container.store.activate_admin_totp,
        user_id,
        secret_ciphertext=ciphertext,
        counter=counter,
        recovery_code_hashes=[recovery_code_digest(code) for code in recovery_codes],
    )
    if not activated:
        raise HTTPException(status_code=409, detail="TOTP enrollment expired; start again")
    return {
        "enabled": True,
        "recovery_codes": recovery_codes,
        "message": "Store these recovery codes now; they will not be shown again.",
    }


@router.post("/totp/disable")
async def disable_totp(
    body: DisableTotpRequest,
    principal: PrincipalDep,
    container: ContainerDep,
):
    user_id = _principal_user_id(principal)
    credential = await asyncio.to_thread(container.store.get_admin_login_credential, user_id)
    if credential is None or not credential.get("totp_enabled"):
        raise HTTPException(status_code=409, detail="TOTP is not enabled")
    password_ok = await asyncio.to_thread(
        verify_password, body.password, credential["password_hash"]
    )
    factor_ok = password_ok and await _verify_active_second_factor(
        container.store, credential, body.code
    )
    if not factor_ok:
        raise HTTPException(status_code=401, detail="password or authentication code is incorrect")
    disabled = await asyncio.to_thread(
        container.store.disable_admin_totp,
        user_id,
        actor_id=principal.subject,
    )
    if not disabled:
        raise HTTPException(status_code=409, detail="TOTP is not enabled")
    return {"enabled": False}


@router.post("/logout")
async def logout(principal: PrincipalDep, container: ContainerDep):
    if principal.subject.startswith("session:"):
        await asyncio.to_thread(
            container.store.revoke_admin_auth_session,
            principal.subject.removeprefix("session:"),
            actor_id=principal.subject,
        )
    return {"logged_out": True}
