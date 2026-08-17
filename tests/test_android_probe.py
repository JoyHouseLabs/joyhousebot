"""Contracts for the Phase-0 Android op probe (hosts/android/probe)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PROBE_PATH = _REPO_ROOT / "hosts" / "android" / "probe" / "android_probe.py"
_FIXTURES = _REPO_ROOT / "hosts" / "android" / "probe" / "fixtures"


def _load_probe():
    spec = importlib.util.spec_from_file_location("android_probe", _PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    import sys

    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def test_build_shell_argv_matches_the_committed_golden_contract() -> None:
    contract = json.loads((_FIXTURES / "op_contract.json").read_text())
    cases = {
        "ui_dump": {"op": "ui_dump", "params": {}},
        "screen_state": {"op": "screen_state", "params": {}},
        "current_app": {"op": "current_app", "params": {}},
        "tap": {"op": "tap", "params": {"x": 540, "y": 1200}},
        "swipe": {"op": "swipe", "params": {"x1": 540, "y1": 1800, "x2": 540, "y2": 600,
                                            "duration_ms": 250}},
        "input_text": {"op": "input_text", "params": {"text": "hello world"}},
        "press_key": {"op": "press_key", "params": {"key": "back"}},
        "launch_app": {"op": "launch_app", "params": {"package": "com.android.settings"}},
        "launch_app_activity": {"op": "launch_app",
                                "params": {"package": "com.android.settings",
                                           "activity": ".Settings$NetworkDashboardActivity"}},
        "wake": {"op": "wake", "params": {}},
    }
    for name, case in cases.items():
        assert probe.build_shell_argv(case["op"], case["params"]) == contract[name], name


def test_unknown_and_invalid_ops_fail_closed() -> None:
    with pytest.raises(probe.ProbeError) as raised:
        probe.build_shell_argv("shell", {"cmd": "rm -rf /"})
    assert raised.value.code == "INVALID_PARAMETERS"
    with pytest.raises(probe.ProbeError):
        probe.build_shell_argv("tap", {"x": -5, "y": 10})
    with pytest.raises(probe.ProbeError):
        probe.build_shell_argv("tap", {"x": "540;rm", "y": 10})
    with pytest.raises(probe.ProbeError):
        probe.build_shell_argv("press_key", {"key": "SHELL"})
    with pytest.raises(probe.ProbeError):
        probe.build_shell_argv("launch_app", {"package": "com.evil; input text hi"})
    with pytest.raises(probe.ProbeError):
        probe.build_shell_argv("input_text", {"text": ""})
    with pytest.raises(probe.ProbeError):
        probe.build_shell_argv("input_text", {"text": "x" * 501})
    with pytest.raises(probe.ProbeError):
        probe.build_shell_argv("input_text", {"text": "你好"})


def test_escape_input_text_transports_spaces_as_percent_s() -> None:
    assert probe.escape_input_text("hello world") == "hello%sworld"
    assert probe.escape_input_text("a+b(c)?!") == "a+b(c)?!"


def test_parse_ui_dump_keeps_document_order_and_flags() -> None:
    xml_text = (_FIXTURES / "window_dump.sample.xml").read_text()
    parsed = probe.parse_ui_dump(xml_text)
    assert parsed["screen"] == {"width": 1080, "height": 2400}
    texts = [node["text"] for node in parsed["nodes"]]
    assert texts == [
        "",
        "",
        "Settings",
        "",
        "Network & internet",
        "",
        "Connected devices",
        "About phone",
    ]
    by_text = {node["text"]: node for node in parsed["nodes"]}
    assert by_text["About phone"]["bounds"] == [132, 2016, 948, 2100]
    assert by_text[""]["scrollable"] is False
    container = parsed["nodes"][5]
    assert container["clickable"] is True
    assert container["resource_id"] == "com.android.settings:id/container"
    assert container["depth"] == 3


def test_parse_ui_dump_truncates_with_flag() -> None:
    xml_text = (_FIXTURES / "window_dump.sample.xml").read_text()
    parsed = probe.parse_ui_dump(xml_text, max_nodes=3)
    assert len(parsed["nodes"]) == 3
    assert parsed["truncated"] is True


def test_parse_screen_state_and_current_app() -> None:
    state = probe.parse_screen_state(
        "settings=$(id) mWakefulness=Awake\nmHoldingDisplaySuspendBlocker=true"
    )
    assert state == {"wakefulness": "awake", "screen_on": True}
    app = probe.parse_current_app(
        "  mCurrentFocus=Window{7f3a u0 com.android.settings/com.android.settings.Settings}"
    )
    assert app == {
        "package": "com.android.settings",
        "activity": "com.android.settings.Settings",
        "component": "com.android.settings/com.android.settings.Settings",
    }
    with pytest.raises(probe.ProbeError):
        probe.parse_current_app("window tree empty")


def _fake_completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def test_probe_run_reports_devices_and_unavailable_adb(monkeypatch: pytest.MonkeyPatch) -> None:
    instance = probe.AndroidProbe(serial="phone-a")

    def fake_run(command, **_kwargs):
        assert command[:4] == ["adb", "-s", "phone-a", "devices"]
        return _fake_completed(
            "List of devices attached\nphone-a\tdevice product:x model:y\n"
        )

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    payload = instance.run("devices", {})
    assert payload["ok"] is True
    assert payload["serial"] == "phone-a"
    assert payload["result"]["devices"] == ["phone-a\tdevice product:x model:y"]

    def missing(command, **_kwargs):
        raise FileNotFoundError("adb")

    monkeypatch.setattr(probe.subprocess, "run", missing)
    payload = instance.run("devices", {})
    assert payload["ok"] is False
    assert payload["error"]["code"] == "ADB_UNAVAILABLE"


def test_probe_ui_dump_falls_back_to_sdcard(monkeypatch: pytest.MonkeyPatch) -> None:
    xml_text = (_FIXTURES / "window_dump.sample.xml").read_text()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(list(command))
        joined = " ".join(command)
        if "uiautomator" in joined and "/dev/tty" in joined:
            return _fake_completed("UI hierchary dumped to: /dev/tty", returncode=0)
        if "dump" in command and _PROBE_PATH.name not in joined:
            return _fake_completed("UI hierchary dumped to: /sdcard/window_dump.xml")
        if "cat" in command:
            return SimpleNamespace(
                stdout=xml_text.encode(), returncode=0, stderr=b""
            )
        return _fake_completed("")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    payload = probe.AndroidProbe().run("ui_dump", {})
    assert payload["ok"] is True, payload
    assert payload["result"]["nodes"], "fallback dump must parse"
    shell_calls = [c for c in calls if "shell" in c]
    assert shell_calls[0][2:] == ["uiautomator", "dump", "/dev/tty"]
    assert ["adb", "shell", "rm", "-f", "/sdcard/window_dump.xml"] in calls


def test_probe_tap_executes_fixed_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def fake_run(command, **_kwargs):
        seen.append(list(command))
        return _fake_completed("")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    payload = probe.AndroidProbe(serial="phone-a").run("tap", {"x": 1, "y": 2})
    assert payload["ok"] is True
    assert seen == [["adb", "-s", "phone-a", "shell", "input", "tap", "1", "2"]]
    assert payload["result"] == {"executed": ["input", "tap", "1", "2"]}


def test_probe_screenshot_writes_file_with_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command, **_kwargs):
        return SimpleNamespace(stdout=b"\x89PNG-fake", returncode=0, stderr=b"")

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    out = tmp_path / "shot.png"
    payload = probe.AndroidProbe().run("screenshot", {"out": str(out)})
    assert payload["ok"] is True
    assert out.read_bytes() == b"\x89PNG-fake"
    assert payload["result"]["size_bytes"] == 9
    assert len(payload["result"]["sha256"]) == 64
