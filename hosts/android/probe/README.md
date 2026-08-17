# Android Host Probe

Phase-0 development harness for the Android Device Host. It drives a locally
connected phone over `adb` to validate the observe/actuate op contract against
real devices **before** the on-device Kotlin executor (Phase 2) is written
against the same JSON shapes.

It is deliberately not a Runtime capability: it is not registered in the
Capability Registry, never appears in `extensions.allowedIds`, and cannot be
reached from any Run.

## Usage

```bash
# read ops
python hosts/android/probe/android_probe.py devices
python hosts/android/probe/android_probe.py ui_dump --max-nodes 200
python hosts/android/probe/android_probe.py screenshot --out shot.png
python hosts/android/probe/android_probe.py screen_state
python hosts/android/probe/android_probe.py current_app

# actuate ops
python hosts/android/probe/android_probe.py tap 540 1200
python hosts/android/probe/android_probe.py swipe 540 1800 540 600 300
python hosts/android/probe/android_probe.py input_text "hello world"
python hosts/android/probe/android_probe.py press_key BACK
python hosts/android/probe/android_probe.py launch_app --package com.android.settings
python hosts/android/probe/android_probe.py wake
```

Every op is a fixed argv template (`build_shell_argv`); there is no shell
string and no arbitrary command surface. The same mapping, charset limits and
JSON shapes are the golden contract for `hosts/android/device-host` and for
`extensions/capability-android-device` input schemas.

## Contract

- Output envelope: `{"ok": bool, "op": str, "elapsed_ms": int, "serial": str|null,
  "result"|"error": ...}` with fail-closed `error.code`.
- `ui_dump` → `{"screen": {"width","height"}, "nodes": [...], "truncated": bool}`;
  nodes are in document order, capped at `max_nodes` (default 200).
- `input_text` accepts a restricted charset and transports spaces as `%s`;
  a literal `%s` in the payload becomes a space on-device (known limitation).
- `press_key` only accepts the `PRESS_KEYS` allowlist; key names are upper-case
  without the `KEYCODE_` prefix.
- `launch_app` validates the package name against
  `^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$` and uses `monkey` when no
  activity is provided.

Fixtures under `fixtures/` are shared with the Python and Kotlin test suites.
