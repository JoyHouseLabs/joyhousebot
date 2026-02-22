"""Network helpers for CLI commands."""

from __future__ import annotations

import errno
import socket


def is_port_in_use(host: str, port: int) -> bool:
    """Return True if the port is already bound (e.g. by another process)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                return True
            raise

