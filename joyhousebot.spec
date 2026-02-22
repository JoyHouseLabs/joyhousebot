# PyInstaller spec for joyhousebot. Run: pyinstaller joyhousebot.spec
# Build per platform (Windows/macOS/Linux) on the target OS or in CI.

import sys

a = Analysis(
    ["joyhousebot/__main__.py"],
    pathex=[],
    datas=[
        ("joyhousebot/skills", "joyhousebot/skills"),  # builtin skills
    ],
    hiddenimports=[
        "joyhousebot.cli.commands",
        "typer",
        "litellm",
        "pydantic",
        "pydantic_settings",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="joyhousebot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# One-folder variant (faster startup, easier to debug): use below instead of EXE above.
# exe = EXE(
#     pyz,
#     a.scripts,
#     [],
#     exclude_binaries=True,
#     name="joyhousebot",
#     debug=False,
#     bootloader_ignore_signals=False,
#     strip=False,
#     upx=True,
#     console=True,
# )
# coll = COLLECT(
#     exe,
#     a.binaries,
#     a.datas,
#     strip=False,
#     upx=True,
#     upx_exclude=[],
#     name="joyhousebot",
# )
