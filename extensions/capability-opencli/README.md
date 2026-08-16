# OpenCLI Capability Extension

`capability-opencli` 是官方 Node Invocation Extension。它把 OpenCLI 的明确网站命令编译成 JoyhouseBot
Capability，不暴露任意命令字符串、argv、Shell、动态插件安装或任意 URL。

## 冻结发布

发布者必须准备精确版本的上游 `cli-manifest.json` 和人工审核后的 allowlist；仓库中的
`catalog/allowlist.pilot.json` 只准入 GitHub 当前账号、X 搜索和需审批的 X 发帖三个试点命令。再运行：

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
  --manifest-sha256 8045376896234011adef4226b395f323d301e1b0613fc7d59b2be5b16f6eff99
```

Catalog 只保留 allowlist 内的命令，并生成严格 input JSON Schema、读写风险、目标域名、浏览器/Profile
要求和不可变 implementation digest。带 `output`、`file`、`path` 等参数的命令默认拒绝编译；确需文件参数
时必须逐字段准入，运行时只允许 operation workspace 内的相对路径。下载文件还必须通过 Runtime 的一次性
Artifact upload grant 回写，不能返回本地路径。

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

每个浏览器命令必须传入明确 `browser_profile_ref`。Cookie 和 Chrome Profile 内容只留在本机；Runtime
只持久化引用。读操作的临时失败最多恢复三次，写操作在不确定边界失败时进入人工复核，绝不自动重放。

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
