"""Signed HTTP connector for business-owned remote capabilities."""

from .connector import (
    HTTP_CAPABILITY_CONNECTOR_MANIFEST,
    RemoteCapabilityTool,
    connect_remote_capabilities,
    create_extension,
    extension_host_manifest_digest,
    preflight_extension_host,
    request_digest,
    sign_request_body,
    sign_response_body,
)

__all__ = [
    "HTTP_CAPABILITY_CONNECTOR_MANIFEST",
    "RemoteCapabilityTool",
    "connect_remote_capabilities",
    "create_extension",
    "extension_host_manifest_digest",
    "preflight_extension_host",
    "request_digest",
    "sign_request_body",
    "sign_response_body",
]
