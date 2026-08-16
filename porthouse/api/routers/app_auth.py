"""Client-authenticated exchange for owner-approved App delegation grants."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from porthouse.api.app_pack_schemas import ExchangeAppTokenRequest
from porthouse.api.dependencies import ContainerDep

router = APIRouter(prefix="/app-auth", tags=["app-auth"])


@router.post("/token")
async def exchange_app_token(body: ExchangeAppTokenRequest, container: ContainerDep):
    result = await container.app_delegation.exchange(**body.model_dump())
    if result is None:
        # Deliberately do not reveal whether client, secret, grant, installation,
        # expiry, or requested scope caused the rejection.
        raise HTTPException(status_code=401, detail="invalid App delegation credentials")
    return result


__all__ = ["router"]
