# OpenCLI Capability Extension

`capability-opencli` 是官方 Node Invocation Extension。它把 OpenCLI 的明确网站命令编译成 joyhousebot
Capability，不暴露任意命令字符串、argv、Shell、动态插件安装或任意 URL。

## 冻结发布

发布者必须准备精确版本的上游 `cli-manifest.json` 和人工审核后的 allowlist；仓库中的
`catalog/allowlist.pilot.json` 准入 GitHub/X 试点命令、只读的小红书账号内容采集，以及微信公众号搜索与文章 Markdown
导入。OpenCLI 的语义账号快照仅保留为可选适配示例，不进入默认 Pilot；JoyHouse 的小红书账号采集
由独立 Python Social Browser Connector 承担。若显式启用该示例，它只接受账号 ID 或
`www.xiaohongshu.com/user/profile/...`，并在 Catalog 与 Worker
两层强制每次 1–20 条。再运行：

```bash
npm ci
npm run build
node dist/cli.js compile-catalog \
  --manifest /release/opencli-1.8.6/cli-manifest.json \
  --allowlist catalog/allowlist.json \
  --output catalog/catalog.json \
  --extension-version 0.1.0 \
  --build-digest sha256:... \
  --lockfile-digest sha256:... \
  --node-version v24.19.0 \
  --opencli-version 1.8.6 \
  --opencli-integrity sha512-... \
  --opencli-entrypoint-sha256 e2c5402693ccb71a04a7793d40ebf3238578d32c8b620edbb38554e0a3d2df07 \
  --manifest-sha256 310a143b41ea677de88f05bfd9c525e3b1e19c14f88d0377356508b161adf3e6
```

Catalog 只保留 allowlist 内的命令，并生成严格 input JSON Schema、读写风险、目标域名、浏览器/Profile
要求和不可变 implementation digest。带 `output`、`file`、`path` 等参数的命令默认拒绝编译；确需文件参数
时必须逐字段准入，运行时只允许 operation workspace 内的相对路径。微信公众号下载是一个严格的例外：
catalog 明确启用后，只会把一个不超过 512 KiB 的 Markdown 内容作为 Runtime Artifact 回传；图片下载会
被调用方关闭，不能返回本地路径、目录或浏览器资料。

## 运行

Extension 由通用 Node Host Supervisor 启动，必需环境变量：

- `OPENCLI_CATALOG_PATH`：冻结 Catalog 的绝对路径；
- `OPENCLI_ENTRYPOINT`：随 Desktop/Server 制品安装的精确 OpenCLI JS entrypoint；
- `OPENCLI_PACKAGE_JSON`：同一已校验 npm 包内的 `package.json`，Worker 会核对名称、版本和入口边界；
- `OPENCLI_STATE_PATH`：本机 operation 幂等与恢复投影；
- `OPENCLI_WORKSPACE_ROOT`：受管的 operation 临时工作区；
- 可选有界配置：`OPENCLI_MAX_CONCURRENCY`、`OPENCLI_MAX_STDOUT_BYTES`、
  `OPENCLI_MAX_STDERR_BYTES`。

Worker 启动时逐字匹配 Catalog 中的 Node 版本。Desktop 使用仓库
`hosts/node/runtime-lock.json` 固定的 Node 24.19.0 LTS，不依赖用户全局 Node/npm。OpenCLI 固定为 1.8.6，
升级必须重新编译 Catalog、形成新的 Host/Capability Revision 并经过 preflight 与 Worker ACK。

每个浏览器命令必须传入明确 `browser_profile_ref`。传入 `auto` 时仅在恰好一个 Browser Bridge
Profile 在线时由 OpenCLI 自动选择；零个或多个 Profile 都会失败关闭。Cookie 和 Chrome Profile 内容只留在本机；
Runtime 只持久化引用。读操作的临时失败最多恢复三次，写操作在不确定边界失败时进入人工复核，绝不自动重放。

本机预检：

```bash
node dist/cli.js preflight \
  --catalog catalog/catalog.json \
  --entrypoint node_modules/@jackwener/opencli/dist/src/main.js \
  --profile chrome-main
```

将不可变 Catalog 渲染为通用 Supervisor 的 Extension 配置片段（不包含签名密钥）：

```bash
node dist/cli.js render-host-extension \
  --bundle-root "$PWD" \
  --state-root /absolute/private/state/opencli
```

渲染前会重新计算 `dist/`、`package-lock.json`、Worker 入口和 OpenCLI 入口摘要；任一文件漂移都会拒绝
启动。输出片段由 Desktop 安装器或部署脚本合并进 Host 配置，Console 不上传 npm 包。

## 退出码

扩展遵循 OpenCLI 公开契约：`0` 成功、`66` 空结果、`69` Browser Bridge 不可用、`75` 临时失败、
`77` 需要登录、`78` 配置错误、`130` 取消。stderr 不进入 Agent 输出，stdout 仅接受有界 JSON。
