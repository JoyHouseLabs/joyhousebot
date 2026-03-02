# hello-native

Minimal python-native plugin example for joyhousebot.

## What it registers

- tool: `echo` - Echo back the input value
- rpc: `greet` - Return a greeting message
- cli: `greet` - CLI command for greeting

## 安装与验证

```bash
# 安装：将本插件加入配置
joyhousebot plugins install ./examples/native-plugins/hello-native

# 验证：列出插件、查看工具
joyhousebot plugins list
joyhousebot plugins tools

# 调用 CLI 命令
joyhousebot plugins cli-run greet --payload '{"name":"joyhouse"}'
```

## Try it（手动配置）

1. Add this folder to `plugins.load.paths` in your config.
2. Enable plugin entry `hello-native` in `plugins.entries`.
3. Run:
   - `joyhousebot plugins list`
   - `joyhousebot plugins tools`
   - `joyhousebot plugins cli-run greet --payload '{"name":"joyhouse"}'`
   - `joyhousebot plugins doctor`

The RPC handler returns:

```json
{ "message": "hello: joyhouse" }
```

Set `plugins.entries.hello-native.config.prefix` to override `"hello"`.
