"""Host-side Android probe for the Device Host op contract.

This is a development harness, not a Runtime capability: it drives a locally
connected phone over `adb` to validate the observe/actuate operation contract
(`hosts/android/probe/fixtures/`) before the on-device Kotlin executor is
written against the same JSON shapes. Every command is a fixed argv template;
there is no shell string and no arbitrary command surface.

Usage:
    python hosts/android/probe/android_probe.py devices
    python hosts/android/probe/android_probe.py ui_dump
    python hosts/android/probe/android_probe.py screenshot --out shot.png
    python hosts/android/probe/android_probe.py tap 540 1200
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

DEFAULT_MAX_NODES = 200
MAX_TEXT_FIELD_CHARS = 120
_UI_DUMP_DEVICE_PATH = "/sdcard/window_dump.xml"

# Keys the actuate contract may press. The device executor must reject
# anything outside this list rather than forwarding raw keyevent names.
PRESS_KEYS = frozenset(
    {
        "BACK",
        "HOME",
        "MENU",
        "ENTER",
        "DEL",
        "TAB",
        "ESC",
        "SPACE",
        "PAGE_UP",
        "PAGE_DOWN",
        "MOVE_HOME",
        "MOVE_END",
        "DPAD_UP",
        "DPAD_DOWN",
        "DPAD_LEFT",
        "DPAD_RIGHT",
        "VOLUME_UP",
        "VOLUME_DOWN",
        "VOLUME_MUTE",
        "POWER",
        "APP_SWITCH",
        "MEDIA_PLAY",
        "MEDIA_PAUSE",
        "MEDIA_NEXT",
        "MEDIA_PREVIOUS",
    }
)

# `input text` on device shells only types this subset reliably. Spaces are
# transported as `%s`; everything outside the set is rejected fail-closed so
# both executors (host adb and on-device Shizuku) share one contract.
_INPUT_TEXT_ALLOWED = re.compile(r"^[A-Za-z0-9 @%:;,. _\-+=/()?!'\"<>#$&*]+$")

_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
_COMPONENT_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+/[^ }\s]+)")
_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_WAKEFULNESS_RE = re.compile(r"mWakefulness=(Awake|Asleep|Dozing|Dreaming)")


class ProbeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def build_shell_argv(op: str, params: dict[str, Any]) -> list[str]:
    """Return the fixed device-side argv for one op; the shared golden contract."""
    if op == "ui_dump":
        return ["uiautomator", "dump", "/dev/tty"]
    if op == "screen_state":
        return ["dumpsys", "power"]
    if op == "current_app":
        return ["dumpsys", "window"]
    if op == "tap":
        _require_ints(params, ("x", "y"), minimum=0)
        return ["input", "tap", str(params["x"]), str(params["y"])]
    if op == "swipe":
        _require_ints(params, ("x1", "y1", "x2", "y2"), minimum=0)
        duration = int(params.get("duration_ms") or 300)
        if not 0 <= duration <= 10_000:
            raise ProbeError("INVALID_PARAMETERS", "duration_ms must be within 0..10000")
        return [
            "input",
            "swipe",
            str(params["x1"]),
            str(params["y1"]),
            str(params["x2"]),
            str(params["y2"]),
            str(duration),
        ]
    if op == "input_text":
        return ["input", "text", escape_input_text(str(params.get("text") or ""))]
    if op == "press_key":
        key = str(params.get("key") or "").strip().upper()
        if key not in PRESS_KEYS:
            raise ProbeError("INVALID_PARAMETERS", f"unsupported key: {key}")
        return ["input", "keyevent", f"KEYCODE_{key}"]
    if op == "wake":
        return ["input", "keyevent", "KEYCODE_WAKEUP"]
    if op == "launch_app":
        package = str(params.get("package") or "").strip()
        if not _PACKAGE_RE.fullmatch(package):
            raise ProbeError("INVALID_PARAMETERS", f"invalid package name: {package!r}")
        activity = str(params.get("activity") or "").strip()
        if activity:
            if not re.fullmatch(r"[A-Za-z0-9_.$]+", activity):
                raise ProbeError("INVALID_PARAMETERS", f"invalid activity name: {activity!r}")
            return ["am", "start", "-n", f"{package}/{activity}"]
        return ["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]
    raise ProbeError("INVALID_PARAMETERS", f"unsupported op: {op}")


def escape_input_text(text: str) -> str:
    if not text:
        raise ProbeError("INVALID_PARAMETERS", "text is required")
    if len(text) > 500:
        raise ProbeError("INVALID_PARAMETERS", "text exceeds 500 characters")
    if not _INPUT_TEXT_ALLOWED.fullmatch(text):
        raise ProbeError(
            "INVALID_CHARACTERS",
            "text contains characters outside the shared input-text charset",
        )
    # `input text` has no quoting layer: a literal "%s" in the payload is
    # typed as a space on-device. The shared contract accepts this limitation.
    return text.replace(" ", "%s")


def _require_ints(params: dict[str, Any], names: Sequence[str], *, minimum: int) -> None:
    for name in names:
        value = params.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ProbeError("INVALID_PARAMETERS", f"{name} must be an integer >= {minimum}")


def extract_ui_xml(output: str) -> str:
    start = output.find("<?xml")
    if start < 0:
        start = output.find("<hierarchy")
    end = output.rfind("</hierarchy>")
    if start < 0 or end < 0:
        raise ProbeError("UI_DUMP_UNAVAILABLE", "uiautomator output did not contain a hierarchy")
    return output[start : end + len("</hierarchy>")]


def parse_ui_dump(xml_text: str, *, max_nodes: int = DEFAULT_MAX_NODES) -> dict[str, Any]:
    """Compress a uiautomator hierarchy into the shared node JSON contract."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProbeError("UI_DUMP_UNPARSEABLE", str(exc)) from exc
    nodes: list[dict[str, Any]] = []
    screen_w = screen_h = 0
    if root.tag == "hierarchy":
        # Real dumps carry screen size on the root node, not the hierarchy tag.
        match = _BOUNDS_RE.search(root.get("bounds") or "")
        if match is None:
            first = root.find("node")
            if first is not None:
                match = _BOUNDS_RE.search(first.get("bounds") or "")
        if match:
            screen_w, screen_h = int(match.group(3)), int(match.group(4))
    truncated = False
    # Pre-order with reversed pushes keeps nodes in document order, which is
    # part of the shared contract (index == position in the hierarchy).
    stack: list[tuple[ET.Element, int]] = [(root, 0)]
    while stack:
        element, depth = stack.pop()
        if element is not root:
            if len(nodes) >= max_nodes:
                truncated = True
                break
            bounds = _BOUNDS_RE.search(element.get("bounds") or "")
            nodes.append(
                {
                    "index": len(nodes),
                    "depth": depth,
                    "class": element.get("class", ""),
                    "resource_id": element.get("resource-id", ""),
                    "package": element.get("package", ""),
                    "text": _clip(element.get("text", "")),
                    "content_desc": _clip(element.get("content-desc", "")),
                    "clickable": element.get("clickable") == "true",
                    "scrollable": element.get("scrollable") == "true",
                    "bounds": (
                        [int(b) for b in bounds.groups()] if bounds else None
                    ),
                }
            )
        stack.extend((child, depth + 1) for child in reversed(list(element)))
    return {
        "screen": {"width": screen_w, "height": screen_h},
        "nodes": nodes,
        "truncated": truncated,
    }


def parse_screen_state(dumpsys_power: str) -> dict[str, Any]:
    match = _WAKEFULNESS_RE.search(dumpsys_power)
    if match is None:
        raise ProbeError("SCREEN_STATE_UNAVAILABLE", "mWakefulness missing from dumpsys power")
    wakefulness = match.group(1)
    return {"wakefulness": wakefulness.lower(), "screen_on": wakefulness == "Awake"}


def parse_current_app(dumpsys_window: str) -> dict[str, Any]:
    for line in dumpsys_window.splitlines():
        if "mCurrentFocus" in line or "mFocusedApp" in line:
            match = _COMPONENT_RE.search(line)
            if match is not None:
                component = match.group(1)
                package, _, activity = component.partition("/")
                if activity.startswith("."):
                    activity = package + activity
                return {"package": package, "activity": activity, "component": component}
    raise ProbeError("CURRENT_APP_UNAVAILABLE", "no focused window in dumpsys window")


def _clip(value: str) -> str:
    return value[:MAX_TEXT_FIELD_CHARS]


@dataclass(slots=True)
class ProbeResult:
    ok: bool
    op: str
    elapsed_ms: int
    serial: str | None
    result: Any = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "op": self.op,
            "elapsed_ms": self.elapsed_ms,
            "serial": self.serial,
        }
        payload["result" if self.ok else "error"] = (
            self.result if self.ok else self.error
        )
        return payload


class AndroidProbe:
    """Runs fixed adb argv templates and returns the shared JSON contract."""

    def __init__(
        self,
        adb_path: str = "adb",
        serial: str | None = None,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.adb_path = adb_path
        self.serial = serial
        self.timeout_seconds = timeout_seconds

    def run(self, op: str, params: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = self._execute(op, params)
            return ProbeResult(
                ok=True,
                op=op,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                serial=self.serial,
                result=result,
            ).to_dict()
        except ProbeError as exc:
            return ProbeResult(
                ok=False,
                op=op,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                serial=self.serial,
                error={"code": exc.code, "message": str(exc)},
            ).to_dict()

    def _execute(self, op: str, params: dict[str, Any]) -> Any:
        if op == "devices":
            return {"devices": self._adb_output(["devices", "-l"]).splitlines()[1:]}
        if op == "screenshot":
            data = self._adb_bytes(["exec-out", "screencap", "-p"])
            out = Path(str(params.get("out") or "screenshot.png"))
            out.write_bytes(data)
            return {
                "path": str(out),
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        if op == "ui_dump":
            xml_text = self._ui_dump_xml()
            return parse_ui_dump(
                xml_text, max_nodes=int(params.get("max_nodes") or DEFAULT_MAX_NODES)
            )
        argv = build_shell_argv(op, params)
        output = self._shell(argv)
        if op == "screen_state":
            return parse_screen_state(output)
        if op == "current_app":
            return parse_current_app(output)
        return {"executed": argv}

    def _ui_dump_xml(self) -> str:
        output = self._shell(build_shell_argv("ui_dump", {}))
        try:
            return extract_ui_xml(output)
        except ProbeError:
            # Some builds refuse /dev/tty; fall back to the sdcard round-trip.
            self._shell(["uiautomator", "dump", _UI_DUMP_DEVICE_PATH])
            xml_text = self._adb_bytes(["exec-out", "cat", _UI_DUMP_DEVICE_PATH]).decode(
                "utf-8", errors="replace"
            )
            self._shell(["rm", "-f", _UI_DUMP_DEVICE_PATH])
            return extract_ui_xml(xml_text)

    def _shell(self, argv: list[str]) -> str:
        return self._adb_output(["shell", *argv])

    def _adb_output(self, argv: list[str]) -> str:
        completed = self._adb(argv)
        if completed.returncode != 0:
            raise ProbeError(
                "ADB_COMMAND_FAILED",
                f"adb {' '.join(argv[:2])} failed: {completed.stderr.strip()[:200]}",
            )
        return completed.stdout

    def _adb_bytes(self, argv: list[str]) -> bytes:
        command = self._adb_prefix() + argv
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProbeError("ADB_UNAVAILABLE", "adb binary was not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("ADB_TIMEOUT", f"adb timed out after {self.timeout_seconds}s") from exc
        if completed.returncode != 0:
            raise ProbeError(
                "ADB_COMMAND_FAILED",
                completed.stderr.decode("utf-8", errors="replace").strip()[:200],
            )
        return completed.stdout

    def _adb(self, argv: list[str]):
        command = self._adb_prefix() + argv
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ProbeError("ADB_UNAVAILABLE", "adb binary was not found") from exc
        except subprocess.TimeoutExpired as exc:
            raise ProbeError("ADB_TIMEOUT", f"adb timed out after {self.timeout_seconds}s") from exc

    def _adb_prefix(self) -> list[str]:
        prefix = [self.adb_path]
        if self.serial:
            prefix += ["-s", self.serial]
        return prefix


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Android Device Host op probe")
    parser.add_argument("--adb", default="adb", help="adb binary path")
    parser.add_argument("--serial", default=None, help="device serial")
    parser.add_argument("--timeout", type=float, default=30.0)
    sub = parser.add_subparsers(dest="op", required=True)
    sub.add_parser("devices")
    dump = sub.add_parser("ui_dump")
    dump.add_argument("--max-nodes", type=int, default=DEFAULT_MAX_NODES)
    shot = sub.add_parser("screenshot")
    shot.add_argument("--out", default="screenshot.png")
    sub.add_parser("screen_state")
    sub.add_parser("current_app")
    tap = sub.add_parser("tap")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    swipe = sub.add_parser("swipe")
    swipe.add_argument("x1", type=int)
    swipe.add_argument("y1", type=int)
    swipe.add_argument("x2", type=int)
    swipe.add_argument("y2", type=int)
    swipe.add_argument("duration_ms", type=int, nargs="?", default=300)
    text = sub.add_parser("input_text")
    text.add_argument("text")
    key = sub.add_parser("press_key")
    key.add_argument("key", choices=sorted(PRESS_KEYS))
    launch = sub.add_parser("launch_app")
    launch.add_argument("--package", required=True)
    launch.add_argument("--activity", default=None)
    sub.add_parser("wake")
    args = parser.parse_args(argv)

    params: dict[str, Any] = dict(vars(args))
    probe = AndroidProbe(adb_path=args.adb, serial=args.serial, timeout_seconds=args.timeout)
    payload = probe.run(args.op, params)
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
