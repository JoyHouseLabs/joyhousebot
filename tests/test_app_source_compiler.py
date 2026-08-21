from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from joyhousebot_package_protocol import cli as package_cli
from joyhousebot_package_protocol import compile_app, validate_app


def _source(root: Path) -> None:
    (root / "joyhousebot" / "agents").mkdir(parents=True)
    (root / "joyhousebot" / "schemas").mkdir(parents=True)
    (root / "joyhousebot.app.toml").write_text(
        """
schema_version = 1
permissions = ["runs.submit", "talent.read_candidate"]

[app]
id = "app.talent-flow"
version = "2.0.0"
name = "Talent Flow"
publisher = "JoyHouse"

[[entrypoints]]
id = "screen-candidate"
name = "Screen candidate"
default = true
input_schema = "schemas/screen.json"

[entrypoints.implementation]
kind = "agent"
ref = "agents/recruiter.json"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "joyhousebot" / "agents" / "recruiter.json").write_text(
        json.dumps(
            {
                "agent_id": "talent.recruiter",
                "revision_id": "talent.recruiter@2.0.0",
                "instructions": "Review structured candidate data.",
            }
        ),
        encoding="utf-8",
    )
    (root / "joyhousebot" / "schemas" / "screen.json").write_text(
        json.dumps({"type": "object", "required": ["candidate_id"]}),
        encoding="utf-8",
    )


def test_source_compiler_is_deterministic_and_writes_frozen_lock(tmp_path: Path) -> None:
    _source(tmp_path)
    first = compile_app(tmp_path)
    second = compile_app(tmp_path)

    assert first.lock == second.lock
    assert first.lock["permission_snapshot"] == ["runs.submit", "talent.read_candidate"]
    assert first.lock["dependency_graph"]["entrypoint:screen-candidate"] == [
        "agents/recruiter.json",
        "schemas/screen.json",
    ]
    destination = first.write()
    assert json.loads(destination.read_text())["editable"] is False
    assert validate_app(tmp_path)["valid"] is True


def test_source_compiler_rejects_missing_references(tmp_path: Path) -> None:
    _source(tmp_path)
    (tmp_path / "joyhousebot" / "schemas" / "screen.json").unlink()

    with pytest.raises(ValueError, match="missing sources"):
        compile_app(tmp_path)


def test_package_protocol_imports_without_runtime_package() -> None:
    package_source = Path(__file__).parents[1] / "packages" / "package-protocol" / "src"
    code = "import joyhousebot_package_protocol; print(joyhousebot_package_protocol.__all__)"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(package_source)!r}); {code}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "compile_app" in result.stdout


def test_operator_publish_requests_carry_explicit_impersonation(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        status_code = 200
        text = "{}"

        @staticmethod
        def json() -> dict[str, object]:
            return {}

    def request(method, url, **kwargs):  # noqa: ANN001
        captured.update(method=method, url=url, **kwargs)
        return Response()

    monkeypatch.setattr(package_cli.httpx, "request", request)
    monkeypatch.setenv("JOYHOUSEBOT_OPERATOR_USER_ID", "publisher-owner")

    package_cli._request("GET", "/control/v1/admin/apps/app.example/releases", "http://runtime", "token")

    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["X-Impersonate-User-ID"] == "publisher-owner"
    assert "Publish versioned App source package" in headers["X-Impersonation-Reason"]


def test_publish_is_idempotent_only_for_the_same_source_lock(tmp_path: Path, monkeypatch) -> None:
    _source(tmp_path)
    compiled = compile_app(tmp_path)

    monkeypatch.setattr(
        package_cli,
        "_request",
        lambda *args, **kwargs: {
            "items": [
                {
                    "version": "2.0.0",
                    "status": "published",
                    "manifest": {
                        "metadata": {"source_lock_digest": compiled.lock["lock_digest"]}
                    },
                }
            ]
        },
    )

    assert package_cli._release_already_published(compiled, "http://runtime", "token")
