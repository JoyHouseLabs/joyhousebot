# hello-native

Minimal python-native plugin example for `joyhousebot`.

## What it registers

- tool: `native.hello.echo`
- rpc: `native.hello.echo`
- hook: `gateway_start`

## 安装与验证（确保整体能运行）

在 joyhousebot 仓库根目录执行：

```bash
# 安装：将本插件加入配置并启用（写入 ~/.joyhousebot/config.json）
joyhousebot plugins install ./examples/native-plugins/hello-native

# 验证：列出原生插件、跑一次 RPC
joyhousebot plugins native-list
joyhousebot plugins cli-run native.hello.echo --payload '{"text":"hi"}'
```

无需单独“编译”原生插件（Python 解释执行）。若未安装/构建 OpenClaw，仅会使用原生插件，整体可正常运行。

## Try it（手动配置）

1. Add this folder to `plugins.load.paths` in your config.
2. Enable plugin entry `hello-native` in `plugins.entries`.
3. Run:
   - `joyhousebot plugins native-list`
   - `joyhousebot plugins cli-list`
   - `joyhousebot plugins cli-run native.hello.echo --payload '{"text":"hi"}'`
   - `joyhousebot plugins doctor`

The RPC handler returns:

```json
{ "message": "hello: <your text>" }
```

Set `plugins.entries.hello-native.config.prefix` to override `"hello"`.

