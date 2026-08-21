# joyhousebot 组件生命周期

本页记录开发期 API、PostgreSQL 表族和后台 Worker 的成熟度。joyhousebot 尚未对外正式服务，因此这里的分级不是兼容承诺，而是删除、重构和发布决策的依据：`Stable` 是当前核心链路；`Experimental` 已进入产品闭环但仍可直接改协议；`Extension-only` 只服务隔离扩展或设备 Host；`Incubating` 仍在验证模型；废弃实现不保留为 `Deprecated`，确认无调用方后直接删除。

## HTTP API

所有进入 OpenAPI 的操作都通过 `joyhousebot.api.lifecycle.TAG_LIFECYCLE` 分类，并在 operation 上输出 `x-joyhousebot-lifecycle`。新增 Router 必须先选择生命周期，否则应用启动失败。

| 生命周期 | Router tag / API 面 |
| --- | --- |
| Stable | `system`、`administrator-auth`、`platform-admin`、`platform-catalog`、`skills`、`runs`、`sessions`、`schedules`、`action-items`、`knowledge`、`memory` |
| Experimental | `apps`、`app-auth`、`app-releases`、`works`、`workflows`、`automation`、`input-assets`、`artifact-uploads`、`run-events` |
| Extension-only | `device-hosts`、`host-tools`、`host-model-grants`、`extension-control-plane`、`remote-capability-connections` |
| Incubating | `agent-teams`、`evaluations`、`experiments`、`model-provider-control-plane`、`embedding-profile-control-plane`、`prompts`、`scenario-studio` |

Stable 只表示它属于当前核心架构，不改变开发阶段允许破坏性调整的事实。Extension-only API 不应被普通 App 直接调用；App 应通过版本化 Run、Artifact、Work 和 Capability 协议接入。

## PostgreSQL 表族

表按独立生命周期和事务边界保留，不按每个 UI 页面建模。事件表只在需要不可变审计、投递重试或状态机回放时独立存在。

| 生命周期 | 表族 | 独立生命周期依据 |
| --- | --- | --- |
| Stable | `runtime_*`、`conversation_sessions`、`request_trace_events`、`capability_*`、`approval_requests`、`operation_reconciliation*`、`configuration_*`、`agent_*`、`skill_*`、`channel_*`、`memory_*`、Knowledge 表 | Run/Task/Turn、调用/审批、发布、投递和私有数据分别有租约、版本或审计边界 |
| Experimental | `app_*`、`works`/`work_*`、`user_workflow*`、`event_trigger*`、`runtime_input_asset*`、`artifact_upload_grants`、`graph_*`、`context_manifest*`、`verification_records` | App 安装/回调、成果发布、自动化、输入物与动态图执行已有独立状态机，但协议仍在收敛 |
| Extension-only | `extension_*`、`extension_inventory`、`remote_connection*`、`device_*`、`host_*` | 只支撑扩展发布、Remote Capability、Device Host 和受治理的宿主能力/模型调用 |
| Incubating | `agent_team_*`、`team_workspace_entries`、`eval_*`、`release_gate_*`、`runtime_experiment*`、`prompt_*`、`scenario_*`、`embedding_profile*`、`replay_runs`、`model_reasoning_segments` | 仍在验证 Team、Eval、实验、Prompt/Scenario Studio、Embedding 配置和诊断回放的产品边界 |

本轮审核未发现可以合并而不破坏事务、租约、审计或不可变版本语义的表。迁移历史不因开发期重构而压缩；如需删除表，新增显式迁移并在专用测试库验证。

## 后台 Worker

| 生命周期 | Worker / 循环 | 所有权 |
| --- | --- | --- |
| Stable | Agent Runtime、Graph Task、Channel bridge、Schedule occurrence、artifact materialization、knowledge maintenance | 统一 Run/Task/Event/Trace 链路及其产物、索引和投递 |
| Experimental | App acquisition/update、App callback、device delivery | App/Market 与设备投递闭环，仍允许直接改任务协议 |
| Extension-only | Host Tool Broker、Extension rollout watcher、Connector/Provider catalog reload | 隔离扩展的准入、执行和 Worker 加载确认 |
| Incubating | Eval execution、Agent monitor reconciliation | 质量门禁与主动 Agent 策略仍在验证 |

Worker 不得另建第二套 Run 状态机。一个后台动作若需要重试、fencing 或人工处理，必须有 PostgreSQL 状态与审计；否则应保持为所属 Worker 内的无状态步骤。

## 本轮清理结论

- API Router 均有实际 application service、控制台/协议用途或契约测试，未删除真实端点。
- 表族均能对应独立版本、租约、审计、投递或隐私边界，未做无依据的合表。
- 删除了 `postgres_migrations.migration_checksum` 兼容导出；测试直接使用真正拥有该职责的 `migration_history` 模块。
- 后续确认废弃项时直接迁移调用方并删除实现，不新增兼容 wrapper、双写或空壳 `Deprecated` 端点。
