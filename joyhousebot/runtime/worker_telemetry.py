"""Small, dependency-free process telemetry for Runtime Worker heartbeats."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any


class ProcessTelemetry:
    """Report a process CPU sample and resident memory without a sidecar."""

    def __init__(self) -> None:
        self._last_wall = time.monotonic()
        self._last_cpu = self._cpu_seconds()

    @staticmethod
    def _cpu_seconds() -> float:
        values = os.times()
        return float(values.user + values.system)

    @staticmethod
    def _rss_bytes() -> int | None:
        """Ask the OS for current RSS; unsupported platforms simply omit it."""
        try:
            result = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(os.getpid())],
                check=False,
                capture_output=True,
                text=True,
                timeout=0.5,
            )
            value = result.stdout.strip()
            return max(0, int(value)) * 1024 if value else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        cpu = self._cpu_seconds()
        elapsed = max(0.001, now - self._last_wall)
        cpu_percent = max(0.0, (cpu - self._last_cpu) / elapsed * 100)
        self._last_wall = now
        self._last_cpu = cpu
        return {
            "pid": os.getpid(),
            "cpu_percent": round(cpu_percent, 1),
            "rss_bytes": self._rss_bytes(),
            "cpu_count": os.cpu_count() or 1,
        }
