# OpenClaw Plugin Host Guide

Joyhousebot uses a built-in **plugin_host** (Node.js sidecar) to seamlessly integrate [OpenClaw](https://github.com/openclaw/openclaw) plugins: it loads the OpenClaw loader (`dist/plugins/loader.js` or `src/plugins/loader.ts`) from a configured workspace directory and bridges plugin registry (tools, channels, providers, hooks, etc.) to joyhousebot without modifying plugin code.

## 1. Overview

- **plugin_host**: A Node.js sidecar shipped with joyhousebot. It talks to the Python side over stdio using line-delimited JSON RPC.
- **OpenClaw workspace**: A directory that contains an OpenClaw clone or compatible project: it must have `package.json` and a loader (`dist/plugins/loader.js` or `src/plugins/loader.ts`).
- **How it works**: You point joyhousebot at that directory via `plugins.openclaw_dir` or the `JOYHOUSEBOT_OPENCLAW_DIR` environment variable. When the gateway or plugin-related commands run, plugin_host starts with that directory as the OpenClaw root, loads the OpenClaw plugin loader, and exposes plugin capabilities (tools, channels, providers, hooks, etc.) to joyhousebot.

## 2. Prerequisites

- **Node.js**: 22+ recommended, with `node` on your PATH.
- **Package manager**: `pnpm` or `npm` (used to run `install` and `build` inside the OpenClaw directory).
- **OpenClaw workspace**: The directory must exist and contain `package.json`, and must have a **loader**: either `dist/plugins/loader.js` (build output) or `src/plugins/loader.ts` (source). Current OpenClaw builds with tsdown **do not** produce a standalone `dist/plugins/loader.js`, so you can use the repo’s `src/plugins/loader.ts`; then run `npm install` in the plugin_host directory so joyhousebot can use tsx to load .ts.

## 3. Configuration

Set the OpenClaw workspace path in joyhousebot (one of the following is enough):

- **Config file**: In your joyhousebot config (e.g. `config.json`), under `plugins`, set `openclaw_dir`:
  ```json
  {
    "plugins": {
      "enabled": true,
      "openclaw_dir": "/path/to/your/openclaw-workspace"
    }
  }
  ```
- **Environment variable**: `JOYHOUSEBOT_OPENCLAW_DIR=/path/to/your/openclaw-workspace`. If `openclaw_dir` is not set in config, plugin_host will use this.

You can also set it via CLI:
```bash
joyhousebot config set plugins.openclaw_dir /path/to/your/openclaw-workspace
```

## 4. Preparing the OpenClaw workspace

1. Clone or prepare an OpenClaw-compatible workspace (e.g. a clone of [openclaw/openclaw](https://github.com/openclaw/openclaw)).
2. From that directory, install dependencies and build:
   ```bash
   cd /path/to/openclaw-workspace
   pnpm install   # or npm install
   pnpm run build # or npm run build
   ```
3. Ensure a **loader** exists: `dist/plugins/loader.js` or `src/plugins/loader.ts`. If you use **src/plugins/loader.ts** (typical for a fresh OpenClaw clone), run `npm install` once in joyhousebot’s plugin_host directory (e.g. `cd site-packages/plugin_host && npm install`) to install tsx; otherwise loading .ts will fail.

## 5. Usage and verification

- **Check environment and paths**: Run the plugins “doctor” to see plugin_host and OpenClaw checks:
  ```bash
  joyhousebot plugins doctor
  ```
  It reports whether host script, openclaw dir, openclaw package.json, openclaw loader, and node/pnpm/npm are present, plus suggestions.

- **Prepare OpenClaw (install + build)**: If the directory is configured but not yet built, you can run:
  ```bash
  joyhousebot plugins setup-host
  ```
  Use `--dry-run` to only print planned commands; use `--no-install` or `--no-build` to skip install or build.

- **List plugins**: After plugin_host has loaded, list bridged plugins:
  ```bash
  joyhousebot plugins list
  ```

- **Reload plugins**: After changing the OpenClaw workspace or plugins:
  ```bash
  joyhousebot plugins reload
  ```

- **Start gateway**: When you start the gateway, plugin_host is started as needed using the configured `openclaw_dir`:
  ```bash
  joyhousebot gateway
  ```

## 6. Troubleshooting

- **Host script missing**: plugin_host was not installed to the expected path. Reinstall joyhousebot from the official wheel/sdist and ensure `site-packages/plugin_host` is present.
- **OpenClaw workspace directory not found**: Check that `plugins.openclaw_dir` or `JOYHOUSEBOT_OPENCLAW_DIR` points to an existing directory.
- **OpenClaw package.json missing**: The directory has no `package.json`; confirm it is a valid OpenClaw (or compatible) workspace.
- **OpenClaw loader missing**: You need either `dist/plugins/loader.js` or `src/plugins/loader.ts` in the OpenClaw directory. If you only have source and use `src/plugins/loader.ts`, run `npm install` in the plugin_host directory to install tsx.
- **node / pnpm / npm not found**: Install Node.js 22+ and add `node` (and `pnpm` or `npm` if needed) to your PATH.
