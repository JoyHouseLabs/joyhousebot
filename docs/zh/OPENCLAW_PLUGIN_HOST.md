# OpenClaw 插件接入说明

joyhousebot 通过内置的 **plugin_host**（Node 侧进程）无缝接入 [OpenClaw](https://github.com/openclaw/openclaw) 插件：在指定 OpenClaw 工作区目录下加载其 loader（`dist/plugins/loader.js` 或 `src/plugins/loader.ts`），将插件列表与能力（工具、通道、Provider、Hooks 等）桥接到 joyhousebot，无需改写插件代码。

## 1. 概述

- **plugin_host**：随 joyhousebot 一起发布的 Node 侧进程，通过 stdio 与 Python 端以行分隔 JSON RPC 通信。
- **OpenClaw 工作区**：一个包含 OpenClaw 源码或克隆的目录，内有 `package.json`，并具备 loader（`dist/plugins/loader.js` 或 `src/plugins/loader.ts`）。
- **接入方式**：在 joyhousebot 配置中指定该目录（`plugins.openclaw_dir` 或环境变量 `JOYHOUSEBOT_OPENCLAW_DIR`），启动网关或执行插件相关命令时，plugin_host 会在该目录下启动并加载 OpenClaw 的 loader，将插件能力暴露给 joyhousebot。

## 2. 前置条件

- **Node.js**：建议 22+，且 `node` 在 PATH 中。
- **包管理器**：`pnpm` 或 `npm`（用于在 OpenClaw 目录下执行 `install` 与 `build`）。
- **OpenClaw 工作区**：已存在且包含 `package.json`；且需具备 **loader** 之一：`dist/plugins/loader.js`（构建产物）或 `src/plugins/loader.ts`（源码）。当前 OpenClaw 使用 tsdown 构建时**不会**单独生成 `dist/plugins/loader.js`，因此可直接使用仓库中的 `src/plugins/loader.ts`；此时需在 plugin_host 目录执行 `npm install` 以安装 tsx，joyhousebot 会使用 tsx 加载 .ts。

## 3. 配置

在 joyhousebot 配置中设置 OpenClaw 工作区目录（二选一即可）：

- **配置文件**：在 `config.json`（或你使用的配置路径）的 `plugins` 下设置 `openclaw_dir`：
  ```json
  {
    "plugins": {
      "enabled": true,
      "openclaw_dir": "/path/to/your/openclaw-workspace"
    }
  }
  ```
- **环境变量**：`JOYHOUSEBOT_OPENCLAW_DIR=/path/to/your/openclaw-workspace`（若未在配置中设置 `openclaw_dir`，plugin_host 会使用该环境变量）。

也可通过 CLI 写入配置：
```bash
joyhousebot config set plugins.openclaw_dir /path/to/your/openclaw-workspace
```

## 4. 准备 OpenClaw 工作区

1. 克隆或准备一个 OpenClaw 兼容的工作区目录（例如 [openclaw/openclaw](https://github.com/openclaw/openclaw) 的克隆）。
2. 进入该目录，安装依赖并构建：
   ```bash
   cd /path/to/openclaw-workspace
   pnpm install   # 或 npm install
   pnpm run build # 或 npm run build
   ```
3. 确认存在 **loader**：`dist/plugins/loader.js` 或 `src/plugins/loader.ts`。若使用 **src/plugins/loader.ts**（当前 OpenClaw 克隆常见情况），需在 joyhousebot 的 plugin_host 目录执行一次 `npm install`（例如 `cd site-packages/plugin_host && npm install`），以安装 tsx，否则加载 .ts 会失败。

## 5. 使用与验证

- **检查环境与路径**：运行插件“医生”命令，查看 plugin_host 与 OpenClaw 目录的检查结果：
  ```bash
  joyhousebot plugins doctor
  ```
  会输出 host script、openclaw dir、openclaw package.json、openclaw loader、node/pnpm/npm 等是否就绪及建议。

- **一键准备 OpenClaw（安装依赖并构建）**：若目录已配置但尚未 `install`/`build`，可执行：
  ```bash
  joyhousebot plugins setup-host
  ```
  仅预览不执行时可加 `--dry-run`；不需要安装依赖或构建时可使用 `--no-install` / `--no-build`。

- **列出插件**：确认 plugin_host 已加载后，可查看桥接的插件列表：
  ```bash
  joyhousebot plugins list
  ```

- **重载插件**：修改 OpenClaw 工作区或插件后，可触发重载：
  ```bash
  joyhousebot plugins reload
  ```

- **启动网关**：正常启动网关后，plugin_host 会在需要时按配置的 `openclaw_dir` 启动并加载插件：
  ```bash
  joyhousebot gateway
  ```

## 6. 常见问题

- **提示 host script 不存在**：说明 plugin_host 未随包安装到预期路径。请确认是通过官方 wheel/sdist 安装的 joyhousebot，且未手动删除 `site-packages/plugin_host`。
- **提示 openclaw workspace directory not found**：请检查 `plugins.openclaw_dir` 或 `JOYHOUSEBOT_OPENCLAW_DIR` 是否指向已存在的目录。
- **提示 openclaw package.json missing**：该目录下缺少 `package.json`，请确认是有效的 OpenClaw 工作区（或兼容的 Node 项目）。
- **提示 openclaw loader 不存在**：需要 OpenClaw 目录下存在 `dist/plugins/loader.js` 或 `src/plugins/loader.ts`。若仅有源码、使用 `src/plugins/loader.ts`，需在 plugin_host 目录执行 `npm install` 以安装 tsx。
- **node / pnpm / npm 未找到**：请安装 Node.js 22+ 并将 `node`（以及需要的 `pnpm` 或 `npm`）加入 PATH。
