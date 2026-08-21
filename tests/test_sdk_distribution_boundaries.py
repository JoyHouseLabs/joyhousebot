from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _isolated_import(source: Path, module: str) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(source)!r}); import {module}; print(sorted(sys.modules))",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def test_extension_sdk_imports_without_runtime_or_vendor_modules() -> None:
    source = Path(__file__).parents[1] / "packages" / "extension-sdk-python" / "src"
    modules = _isolated_import(source, "joyhousebot_extension_sdk")
    assert "joyhousebot.runtime" not in modules
    assert "joyhousebot.storage" not in modules
    assert "httpx" not in modules


def test_app_sdk_imports_without_runtime_or_database_modules() -> None:
    source = Path(__file__).parents[1] / "sdks" / "python" / "src"
    modules = _isolated_import(source, "joyhousebot_sdk")
    assert "joyhousebot.runtime" not in modules
    assert "psycopg" not in modules


def test_runtime_dockerfile_materializes_uv_workspace_before_install() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")
    install_at = dockerfile.index("uv pip install --system --no-cache '.[observability]'")
    for workspace in (
        "COPY packages/package-protocol/ packages/package-protocol/",
        "COPY packages/extension-sdk-python/ packages/extension-sdk-python/",
        "COPY sdks/python/ sdks/python/",
    ):
        assert dockerfile.index(workspace) < install_at
