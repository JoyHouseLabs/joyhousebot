# 多模态与检索增强 Extension 契约

状态：Accepted（2026-08-15）
适用范围：官方与第三方技术 Extension

## 决策

OCR、图像/视频理解、Embedding Provider 与 Reranker 都不是 Runtime Core 功能。它们会随模型、价格、部署
地区和数据策略变化，必须作为可安装、可启停、可回滚的 Extension 接入。Core 只负责：冻结输入、授予最小
权限、调度 Capability、记录 Trace/成本/证据，并把结果写为私有 Artifact 或受范围限制的 Knowledge revision。

这条边界避免把特定视觉模型、OCR SDK 或搜索供应商耦合进 Run/Task 状态机，同时保证每次调用仍可复盘。

## 官方能力分层

| Extension 类型 | 稳定 Capability | 输入 | 受治理输出 | 禁止事项 |
| --- | --- | --- | --- | --- |
| `capability-document-processing` | `document.extract` | 当前 Run 已冻结的 Input Asset | 带页码/偏移证据的文本 Artifact | 直接读取宿主路径、建业务表、在 Worker 进程内解析 |
| `capability-vision` | `vision.understand`（含 OCR 模式） | 私有图片 Asset ref 与明确任务 | 结构化观察、区域证据、置信度 | 把原始二进制或个人图像写入日志；未经授权向第三方上传 |
| `provider-embedding-*` | 由 Core 的 Embedding Profile 调用 | 受限文本 chunk | 向量、模型/version、token/cost 用量 | 绕过 Profile 直接向知识表写向量 |
| `capability-rerank-*` | `retrieval.rerank` | query 与最多 N 个已授权候选 | 排序、相关性分数、模型/version、降级标志 | 越权检索；把候选内容留存为供应商训练数据 |

`capability-context-assets` 继续拥有 Knowledge 的范围校验、版本 staging 与检索编排；它可以选择一个已发布的
Embedding Profile 或 Agent Revision 冻结的 Rerank ref，但不拥有任何供应商 SDK 或密钥。当前 Rerank ref 位于
Agent `memory_policy.retrieval.rerank`：必须显式启用、固定 `retrieval.rerank` 的精确版本，并可选择
`fallback` 或 `fail_closed`。嵌套重排仍经 Core Dispatcher，产生独立的 Capability invocation/Trace。

## `vision.*` 结果契约

Vision Extension 应接受由 Runtime 解析后的 Asset 引用，不接受文件系统路径和任意 URL。每项观察至少包含：

```json
{
  "kind": "text|object|scene|safety_signal",
  "value": "…",
  "confidence": 0.0,
  "evidence": {"asset_id": "asset_…", "page": 1, "region": [0.1, 0.2, 0.3, 0.4]},
  "model": {"provider": "…", "model": "…", "version": "…"}
}
```

`confidence` 只表示供应商或模型给出的置信信息，不能被表述为事实保证。输出体积受 Capability schema 限制；大文本、
帧图或中间结果应写入私有 Artifact，并由返回值引用。涉及人脸、身份证、医疗或其他敏感内容时，由部署策略拒绝或
要求人工审批，不能由 Extension 私自放宽。

## `retrieval.rerank` 结果契约

Rerank 只能重排已经由 Core/Context Assets 在相同 `user_id + agent_id + root_run_id` 范围内选出的候选，不能把
它作为第二个任意搜索入口。输入包含 `query`、受上限约束的 `candidates[]` 和精确的 profile/version；输出为：

```json
{
  "ranked": [{"candidate_id": "…", "score": 0.0, "rank": 1}],
  "model": {"provider": "…", "model": "…", "version": "…"},
  "fallback": false
}
```

分数只在同一次、同一模型版本的排序中比较。若 Reranker 不可用，是否退回向量/词法排序由已发布的检索 Profile
显式声明；否则失败关闭。无论正常或降级，都要在 Trace 中记录 profile、候选数量、延迟、token/cost 与原因。

## 配置、发布与评估

每个 Extension 都必须提供 Manifest、配置 Schema、permissions、出站声明、健康检查及卸载测试。部署者先在
`extensions.allowedIds` 允许代码，再由控制面发布并等待 Worker ACK；未被 ACK 的 build 不可执行。

视觉模型、Embedding Profile 和 Rerank Profile 都应是可版本化的配置目标：先以 Eval 测试真实的证据质量、延迟
和成本，再通过 release gate 发布。需要在线对比时，使用 Runtime Experiment 的稳定分桶与护栏，而不是在业务 App
里自行随机或直接切换模型。

## 首批实现顺序

1. 复用现有 `capability-document-processing` 与 `capability-context-assets`，先以文本解析/向量检索保证基础闭环；
2. 新建 `capability-rerank-*`，实现上述窄输入输出协议，并由检索 Profile 选择；
3. 已提供 `capability-vision` 的 OpenAI-compatible 基线：只接受冻结图片 Asset，输出带证据的观察；视频和其他协议仍以独立 Extension 追加；
4. 为每个 Provider 适配器补 Eval Suite、成本指标和部署 allowlist 示例。

这些步骤均不改变 Core 的数据库 schema 或 Run/Task 状态机，也不把任何垂直业务判断带入 Runtime。
