# Porthouse 复杂度重构计划

## 目标

在不破坏 PostgreSQL 唯一事实源、统一 Run/Task/Event/Trace 链路、权限、审批、幂等、审计和恢复语义的前提下，直接重构开发期内部结构，不保留尚未发布的旧 Python API、模块路径或兼容 wrapper。

复杂度规则以根 `AGENTS.md` 为准：常规文件目标 600 行、硬上限 900 行；函数目标 100 行、硬上限 200 行；分支复杂度目标 20、硬上限 40；嵌套目标三层、默认不得超过四层。`scripts/check_complexity.py` 和 `scripts/complexity_baseline.json` 实施基线递减门禁。

## 实施原则

1. 不重写业务语义；以已有状态机和测试为行为基线。
2. 不为开发期旧结构增加 shim、双写或平行状态机；迁移调用方后立即删除旧路径。
3. 文件长度不是机械拆分目标；优先降低状态数量、分支、隐式依赖和巨型接口。
4. PostgreSQL Repository 可以拆成窄接口，但共享连接池、Unit of Work 和原子事务不拆散。
5. 未经单独确认不删除本地数据、不清空数据库、不压缩已有迁移历史。
6. 每完成一个热点就同步降低复杂度基线，不能把已消除的豁免保留为未来额度。

## 阶段 0：规则与验证入口

状态：已完成。

- 统一根 `AGENTS.md`、架构文档与架构测试的复杂度标准。
- 增加生产 Python、Extension 和 Vue 文件的复杂度扫描。
- 将复杂度检查接入 `pre_release_check.sh`。
- 标准完整测试在运行前安装仓库内 Extension entry points。
- CI 允许历史债务存在，但拒绝新增或恶化的硬阈值违规。

## 阶段 1：执行链热点拆解

状态：已完成。

按执行风险和复杂度排序：

1. Request Coordination（已完成）：将授权、Skill 绑定、Team 范围、目录加载、规划、追问和图物化改为显式准备管线。
2. Agent Turn Engine（已完成）：引入 `TurnLoopState`，分离模型调用、Usage、Tool Batch、Durable Journal、验证和终态。
3. Workflow Compiler（已完成）：分离 catalog/authorize、节点 normalize、版本冻结、Graph validate 和 Task compile；Workflow 用例服务不再承载编译器状态机。
4. Run Submission（已完成）：分离规范化、authority、limits、lineage、snapshot、原子事务提交、事件初始化和唤醒；删除 Graph 非原子兼容写入路径。
5. Agent Execution / Graph Finalization（已完成第一轮）：拆出调用上下文、事件桥接、生命周期转换、节点路由和失败策略；Agent Run 与 Graph Task 顶层编排函数均已降到 100 行内。

每个热点的完成条件：

- 顶层编排函数不超过 100 行；
- 所有新增步骤不超过 200 行、复杂度不超过 40；
- 相关豁免从基线删除；
- 相关状态机测试、完整 Ruff 和复杂度检查通过。

## 阶段 2：Storage 窄接口与组合

状态：已完成。

目标是删除 271 方法的统一 Store 协议和多层 PostgreSQL mixin 聚合。按事务域定义窄接口：

- Run / Task / Event / Graph；
- Capability / Invocation / Approval；
- Artifact / Work / Verification；
- Schedule / Channel / Operations；
- Configuration / Rollout / Extension / App。

计划结构：

```text
porthouse/storage/
├── contracts/
├── postgres/
│   ├── database.py
│   ├── unit_of_work.py
│   ├── migrations/
│   └── repositories/
└── stores.py
```

Runtime 使用显式 `RuntimeStores` 依赖对象。每个 Store Protocol 原则上不超过 30 个方法；具体 PostgreSQL Repository 共享连接池和 Unit of Work。按事务域一次性迁移消费者和测试，完成一组就删除对应旧 mixin，不长期保留双路径。

Runtime 执行侧首批迁移已完成：

- 建立 Run、Task、Event、Graph、Worker、Agent Catalog、Scenario、Execution、Planning、Plan Confirmation、Observability、Reconciliation、Experiment、Team Workspace、Trace、Log 和 Maintenance 窄端口，全部投影到同一个事务后端；
- Submission、Coordinator、Controls、Agent Execution、Graph Task Execution 及其节点处理器、Request Coordination、Graph Materialization、Graph Finalization、Event Broker、Work Signal 和 Maintenance 已改用显式端口视图；
- Graph 提交强制使用 `create_runtime_graph` 原子入口，不再探测方法并退回 Run/Task 分步写入；
- 架构测试禁止已迁移服务重新访问 `self.store`，端口方法数门禁为不超过 30；
- `NativeAgentRuntime` 已删除过渡性的统一 `self.store` 字段，只持有显式 `RuntimeStores` 视图；Event Broker 也不再动态探测可选存储方法；
- Application 的 Run 创建/查询/计划确认链已迁移到 Run、Scenario、Clarification、Catalog、Plan、Experiment 和 Invocation 端口；`RunService.create` 已拆为独立用例服务，删除输入已完整后重复进入追问的不可达分支；
- Workflow 编译器与 Workflow CRUD/执行链已迁移到 Catalog、Scenario、Run 和 Workflow 端口；编译器通过不可变 catalog/context 冻结 Agent、Team、Scenario、Skill 与 Capability 版本；
- GraphPatch 已拆为纯准备管线与原子 propose/apply 服务，快照范围、DAG/Saga、风险和审批校验在写事务前完成；整个链只依赖 Catalog 与 GraphPatch 端口；
- App Pack 依赖校验已按 Core、Extension、Capability、Agent、Team、Skill、Scenario、Workflow、Integration 和 Entrypoint 拆分；验证器只使用只读窄端口并集中生成不可变 dependency lock；
- 统一 `RuntimeStore` Protocol 已删除；PostgreSQL 根 Store 不再继承 61 个 mixin，而是组合 Operations、Runtime、Graph、Catalog、Governance、Asset、Device 和 Configuration Repository group。
- `PostgresRuntimeStore.runtime_stores()` 返回显式窄视图；控制面暂由组合 facade 路由到唯一 Repository owner，不再用继承顺序决定方法归属。

## 阶段 3：Runtime 与 Agent 去 mixin

状态：已完成。

`NativeAgentRuntime` 已组合 Submission、Agent Execution、Coordination、Request Coordination 和 Control 服务；`NativeAgentExecutor` 已组合 Model、Tool、Turn、Message 和 Memory 服务。核心类只保留依赖、生命周期与明确的公共入口，不再从业务 mixin 隐式获得方法和字段。

完成条件：

- 架构测试固定核心 Runtime、Agent 与 PostgreSQL Store 的无业务 mixin 基类；
- Runtime 使用 `RuntimeStores` 窄端口，组合服务使用具体 composition root 类型；
- Storage 端口均不超过 30 个方法，并由测试核对 PostgreSQL 视图完整实现；
- 公共入口继续保留在 composition root，内部测试和实现直接访问具名服务。

## 阶段 4：产品面与控制台收敛

状态：已完成。

- API 的每个 OpenAPI operation 均输出 `x-porthouse-lifecycle`；未分类 Router 会在应用组装时失败。
- 表族和 Worker 的生命周期、独立状态依据与删除准则记录在 `COMPONENT_LIFECYCLE.md`。
- 审核未发现可以在不破坏事务、租约、审计、投递或不可变版本语义下合并的表；删除了仅为旧导入路径存在的 migration checksum wrapper。
- `Agents.vue` 将版本草稿 hydration/serialization 拆到独立 composable 模块，由 851 行降至 711 行；API 和错误适配仍集中在 `src/api/`。

## 验证与交付

每一批重构至少运行：

```bash
bash scripts/install-test-extensions.sh
.venv/bin/python scripts/check_complexity.py --check
.venv/bin/python -m pytest
.venv/bin/ruff check porthouse tests extensions/*/src scripts/check_complexity.py
cd apps/console && npm run typecheck && npm run build
```

交付说明必须报告复杂度基线变化、测试结果、是否修改 HTTP/Event/Schema、是否涉及数据迁移，以及被删除的旧路径。
