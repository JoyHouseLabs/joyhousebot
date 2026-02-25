"""HTTP endpoints for cloud connect functionality."""

from __future__ import annotations

import hashlib
import platform
import socket
import time
from typing import Any

from joyhousebot.identity import ensure_bot_identity, sign_bot_challenge
from joyhousebot.storage import LocalStateStore
from joyhousebot.config.loader import get_data_dir
from pathlib import Path


def _get_house_identity_key_path() -> Path:
    """Path to Ed25519 house identity key."""
    return get_data_dir() / "keys" / "house_identity_ed25519.hex"


def _machine_fingerprint() -> str:
    """Generate machine fingerprint for registration."""
    raw = "|".join(
        [
            socket.gethostname(),
            platform.platform(),
            str(Path.home()),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_house_identity_response(*, store: Any) -> dict[str, Any]:
    """Get house identity info."""
    identity = store.get_identity()
    if not identity:
        return {
            "ok": False,
            "message": "House identity not initialized. Run 'joyhousebot house init' first.",
        }

    return {
        "ok": True,
        "data": {
            "house_id": identity.house_id,
            "house_name": identity.house_name or "",
            "machine_fingerprint": _machine_fingerprint(),
            "public_key": identity.identity_public_key,
            "registered": identity.status != "local_only",
            "bound_user_id": identity.bound_user_id if hasattr(identity, "bound_user_id") else None,
            "created_at": identity.created_at if hasattr(identity, "created_at") else time.time(),
        },
    }


def register_house_response(*, store: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Register house to backend."""
    identity = ensure_bot_identity(_get_house_identity_key_path())
    challenge = f"register:{identity.public_key_hex}:{int(time.time())}"
    signature = sign_bot_challenge(identity.private_key_hex, challenge)

    server_url = body.get("server_url", "http://127.0.0.1:8000")
    house_name = body.get("house_name", "joyhouse-local")
    user_id = body.get("user_id")

    from joyhousebot.control_plane import ControlPlaneClient

    try:
        client = ControlPlaneClient(server_url)
        data = client.register_house(
            house_name=house_name,
            machine_fingerprint=_machine_fingerprint(),
            identity_public_key=identity.public_key_hex,
            challenge=challenge,
            signature=signature,
            capabilities=["task.execute.v1", "task.report.v1", "skills.sync.v1"],
            feature_flags=["sqlite-local-state", "ed25519-identity"],
            owner_user_id=user_id.strip() if user_id else None,
        )
        house_id = str(data.get("house_id", ""))
        if not house_id:
            return {
                "ok": False,
                "message": "Registration failed: missing house_id in response",
            }

        store.upsert_identity(
            identity_public_key=identity.public_key_hex,
            house_id=house_id,
            status="registered",
            access_token=str(data.get("access_token") or "") or None,
            refresh_token=str(data.get("refresh_token") or "") or None,
            ws_url=str(data.get("ws_url") or "") or None,
            server_url=server_url,
        )

        return {
            "ok": True,
            "message": "House registered successfully",
            "house": {
                "house_id": house_id,
                "house_name": house_name,
                "machine_fingerprint": _machine_fingerprint(),
                "public_key": identity.public_key_hex,
                "registered": True,
                "bound_user_id": user_id.strip() if user_id else None,
                "created_at": time.time(),
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Registration failed: {str(e)}",
        }


def bind_house_response(*, store: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Bind house to a user."""
    identity = store.get_identity()
    if not identity or not identity.house_id:
        return {
            "ok": False,
            "message": "House not registered. Please register first.",
        }

    house_id = identity.house_id
    user_id = body.get("user_id")
    if not user_id:
        return {
            "ok": False,
            "message": "User ID is required",
        }

    from joyhousebot.control_plane import ControlPlaneClient

    try:
        server_url = body.get("server_url", "http://127.0.0.1:8000")
        client = ControlPlaneClient(server_url)

        house = client.get_house(house_id)
        owner = house.get("owner_user_id") or house.get("ownerUserId")
        if owner:
            return {
                "ok": False,
                "message": f"House already bound to user: {owner}",
            }

        client.bind_house(house_id=house_id, owner_user_id=user_id)

        return {
            "ok": True,
            "message": "House bound to user successfully",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Bind failed: {str(e)}",
        }


def get_cloud_connect_status_response(*, store: Any) -> dict[str, Any]:
    """Get cloud connection status."""
    try:
        worker_status = store.get_sync_json(name="control_plane.worker_status")
        if not worker_status:
            return {
                "ok": True,
                "data": {
                    "connected": False,
                    "authenticated": False,
                    "house_id": None,
                    "last_connected": None,
                    "error": "Worker not started",
                },
            }

        return {
            "ok": True,
            "data": {
                "connected": worker_status.get("wsActive", False),
                "authenticated": worker_status.get("running", False),
                "house_id": worker_status.get("houseId"),
                "last_connected": worker_status.get("lastHeartbeatMs"),
                "error": worker_status.get("lastHeartbeatError"),
            },
        }
    except Exception as e:
        return {
            "ok": False,
            "data": {
                "connected": False,
                "authenticated": False,
                "house_id": None,
                "last_connected": None,
                "error": str(e),
            },
        }


def start_cloud_connect_response(*, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start cloud connect worker."""
    try:
        import subprocess
        import sys

        backend_url = body.get("backend_url", "http://127.0.0.1:8000") if body else "http://127.0.0.1:8000"
        cmd = [sys.executable, "-m", "joyhousebot", "house", "worker", "--server", backend_url]

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        return {
            "ok": True,
            "message": f"Cloud connect worker started (PID: {process.pid})",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Failed to start worker: {str(e)}",
        }


def stop_cloud_connect_response(*, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stop cloud connect worker."""
    try:
        from joyhousebot.storage import LocalStateStore

        store = LocalStateStore.default()
        worker_status = store.get_sync_json(name="control_plane.worker_status")

        if not worker_status or not worker_status.get("running", False):
            return {
                "ok": True,
                "message": "Worker not running",
            }

        return {
            "ok": True,
            "message": "Worker stopped. Please manually terminate the process.",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Failed to stop worker: {str(e)}",
        }
