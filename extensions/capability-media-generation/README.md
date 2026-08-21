# Media Generation Capability

joyhousebot 的可选媒体生成扩展。它通过稳定的 Capability 协议提供：

- `image.generate`：文生图；
- `image.edit`：图生图/多图编辑；
- `video.generate`：文生视频与图生视频。

当前内置两个适配器：

- `volcengine_ark`：Seedream 图片生成/编辑与 Seedance 视频生成；
- `jimeng`：即梦图片生成 4.0 与即梦视频生成 3.0。

协议字段以火山引擎官方文档为准：[方舟图片生成](https://api.volcengine.com/api-docs/view?action=ImageGenerations&serviceCode=ark&version=2024-01-01)、
[方舟视频任务](https://api.volcengine.com/api-explorer/?action=CreateContentsGenerationsTasks&groupName=%E8%A7%86%E9%A2%91%E7%94%9F%E6%88%90API&serviceCode=ark&version=2024-01-01)、
[即梦图片 4.0](https://www.volcengine.com/docs/85621/1863351) 和
[即梦视频 3.0](https://www.volcengine.com/docs/85621/1791184?lang=zh)。

方舟模型 ID 是运行时配置，不绑定扩展版本，因此可以在 Console 将 `ark_video_model` 设置为账号当前已
开通的 Seedance Model ID 或推理 Endpoint ID。扩展不猜测尚未在账号模型列表中可用的版本名称。

## 安装与启用

```bash
uv pip install -e extensions/capability-media-generation
```

在 `config.json` 中将扩展加入部署 allowlist；首次安装时如需自动激活，再写入
`initiallyActive`：

```json
{
  "extensions": {
    "allowedIds": [
      "provider-openai-compatible",
      "capability-media-generation"
    ],
    "initiallyActive": [
      "provider-openai-compatible",
      "capability-media-generation"
    ]
  }
}
```

运行 `joyhousebot discover-extensions --config config.json` 后启动或重启 Agent Worker。扩展被发现和发布后，在 Console 的
`Extensions → Media Generation → Capability 配置` 中选择默认供应商和即梦
`req_key`。Console 会独立显示 Runtime/Worker 状态和 Worker 凭据检查。Agent revision 还需显式允许
三个 Capability，并授予 `media.generate` 权限；只安装 Extension 或填写模型参数不会自动扩大 Agent 权限。

## 凭据

凭据只能放在 Worker 环境变量，不能写入 Console：

```bash
# 火山方舟（Seedream / Seedance）
export VOLCENGINE_ARK_API_KEY='...'

# 即梦 OpenAPI（也兼容官方 SDK 使用的 VOLC_ACCESSKEY / VOLC_SECRETKEY）
export VOLC_ACCESSKEY='...'
export VOLC_SECRETKEY='...'
```

可选 STS Token：`VOLC_SESSION_TOKEN`。

## 运行语义

媒体生成会产生外部费用，因此三个 Capability 均声明为外部写操作并默认要求 Owner 确认。
Seedance 和即梦异步任务返回 `accepted`，Runtime 持久化供应商任务 ID，由现有 Operation
Reconciliation Worker 查询状态并在完成后生成 `media.image` 或 `media.video` Artifact。

火山媒体接口没有公开承诺基于客户端键去重。扩展仍传递 Runtime 冻结的
`Idempotency-Key` 以便供应商支持时使用，但能力元数据明确声明 `idempotent=false`、
`retryable=false`：提交结果未知时进入人工处理，不能自动重提并造成重复计费。

Seedream 与即梦返回的图片 URL 通常有效 24 小时，即梦视频 URL 通常有效 1 小时，Seedance
视频 URL 也属于临时下载地址。Artifact 会明确记录 `source_is_ephemeral` 与已知有效期；生产部署
应再安装对象存储扩展，把临时 URL 物化到用户自己的长期存储，不应把供应商临时 URL 当作公开
Work 链接。

## 新供应商适配

新增供应商时实现 `MediaProviderAdapter`，并注册进 `MediaProviderRegistry`。适配器必须：

1. 只从服务器环境读取密钥；
2. 消费 Runtime 冻结的 Action/幂等身份；
3. 长任务返回可查询的 operation descriptor；
4. 在最终结果中返回 Artifact，而不是绕开 Runtime 写数据库；
5. 对供应商不保证幂等的接口禁止自动重提。
