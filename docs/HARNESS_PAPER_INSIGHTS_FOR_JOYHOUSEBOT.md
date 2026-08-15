# 从 DeepSeek Harness 论文看 AI Runtime 的下一层：JoyhouseBot 的优势、短板与改进路线

最近读到北京大学与 DeepSeek-AI 研究者合作的论文《A Programming Paradigm for Spatiotemporal Composability》。

它不是一份“DeepSeek 发布了一个新 Agent 产品”的说明书，而是在讨论一个更底层的问题：

> 当一个软件系统允许组件在运行中不断加入、退出和替换时，怎样保证旧组件留下的副作用可以被完整清理，依赖它的其他组件也能自动进入正确状态？

论文把这个问题称为“时空可组合性”，并实现了一个名为 Cordis 的元框架。它也把“自进化 Agent Harness”列为最值得继续验证的方向之一。

这篇论文让我重新审视了 JoyhouseBot。

我的结论是：JoyhouseBot 当前选择的方向没有错。我们在长期任务、外部业务副作用和分布式治理上走得更远；但如果希望成为 AI 时代类似 WordPress 的开放 Runtime，我们还缺一层真正成熟的 Extension 运行时。

## 一、论文解决的不是 Agent 能力，而是组件生命周期

今天讨论 Agent Infra 时，人们通常关注模型、Prompt、Tool、Memory、RAG 和多 Agent Workflow。

但当一个 Runtime 开始拥有大量 Provider、Connector、Channel 和第三方 Extension 后，另一个问题会迅速出现：这些能力如何在不重启整个系统的情况下安全变化？

Cordis 从两个维度回答这个问题。

### 1. 时间可组合性

组件执行一个副作用时，同时向 Runtime 提供对应的逆操作。

例如：

- 注册事件监听器，同时登记取消注册的方法；
- 建立连接池，同时登记关闭连接池的方法；
- 启动后台任务，同时登记停止和等待任务退出的方法；
- 注册一个服务，同时登记撤销服务的方法。

Runtime 统一追踪这些逆操作。组件卸载时，系统按相反顺序自动执行清理，而不是依赖开发者另外维护一份容易遗漏的 `deactivate()`。

### 2. 空间可组合性

组件明确声明自己提供什么、依赖什么。

依赖满足时，组件自动激活；依赖消失时，组件自动停用。如果一个 Provider 要退出，Runtime 会先让依赖它的 Consumer 完成清理，再卸载 Provider。

这不是业务 Workflow 的任务依赖，而是 Runtime 内部代码组件的依赖拓扑。

在此基础上，Cordis 又实现了组件隔离、访问拦截、声明式配置调和和事务式热模块替换。

## 二、这篇论文让我意识到：Agent Runtime 有两类完全不同的副作用

这是我认为最重要的启发。

第一类是 Runtime 内部副作用：连接、监听器、注册表、后台任务、缓存和进程内资源。Cordis 擅长处理这一类，它们通常可以通过 disposer 或 inverse 恢复。

第二类是外部业务副作用：发送邮件、发布内容、退款、修改客户资料、创建工单和调用企业系统写接口。一旦数据已经离开 Runtime，就不能假装一个普通函数可以让世界回到原状。

论文也明确承认这一边界。外部 emission 往往只能延迟提交，或者使用业务补偿。

JoyhouseBot 当前真正有价值的地方，恰恰是第二类副作用的治理：

- 每次写操作都有冻结的 `action_id` 和 `idempotency_key`；
- 高风险动作进入人工审批；
- Worker 崩溃后可以查询外部操作究竟成功、失败还是未知；
- 未知结果进入 Reconciliation，而不是盲目重试；
- 执行、投递、回执、证据和审计进入同一条持久链路。

因此，Cordis 和 JoyhouseBot 不是互相替代的关系。

> Cordis 式机制管理 Worker 内部资源；JoyhouseBot 的 Action 与 Reconciliation 管理外部业务世界。

一个真正可靠的 Agent Runtime 最终需要同时具备两者。

## 三、JoyhouseBot 已经做得更好的地方

### 1. 长期任务不是进程内 Promise，而是数据库事实

JoyhouseBot 以 PostgreSQL 保存 Run、Task、Event、Trace 和 Artifact。Worker 使用 lease、fencing 和数据库时钟取得执行权；进程退出后，其他 Worker 可以接管未完成工作。

这与 Cordis 的进程内 Context/Fiber 是不同层次的问题。Cordis 让组件可以安全离开一个运行中的进程，JoyhouseBot 让整个进程消失以后任务仍然存在。

### 2. 外部写入具有审批、幂等和对账闭环

Agent 最危险的部分从来不是“回答错一句话”，而是对真实业务系统产生了不可逆操作。

JoyhouseBot 不把 Tool 调用当成一个普通 Python 函数。写操作必须经过 Capability Dispatcher、权限、配额、审批、幂等、回执和对账。未知状态不会被伪装成失败或成功。

### 3. 发布物具有不可变身份

Agent、Skill、Scenario、Capability 和 Extension 都使用明确版本。Capability 进一步绑定 Extension 版本和 build digest；Run 接受时冻结执行快照，重试与回放不会静默漂移到“最新版本”。

### 4. 发布是集群状态机

JoyhouseBot 已经有 staged、Worker 预热、逐机 ACK、激活、失败保留旧版本和受治理回滚。它解决的是多 Worker 环境中的发布一致性，而不是单进程开发期 HMR。

### 5. Agent 执行已经形成完整业务链

聊天、定时任务、Channel 入站、Webhook、MCP、多 Agent Team 和 Workflow 最终都进入同一条 Run/Task/Event 链路。Artifact 可以继续沉淀为不可变 Work，而不是执行完成以后只剩一段聊天文本。

这些是 JoyhouseBot 应该继续坚持的核心，而不是为了追逐新的论文概念重新推倒。

## 四、JoyhouseBot 真正落后的地方

### 1. Plugin 注册目前基本是单向的

当前 Capability Plugin 会把 Handler 和定义注册到内存 Registry，但没有统一的、结构化的卸载协议。

Runtime 不知道一个 Extension 在注册过程中启动了哪些后台任务、打开了哪些连接、订阅了哪些事件，也无法证明卸载后已经恢复到加载前状态。

### 2. Worker ACK 还不等于动态加载

今天的 Plugin rollout 可以验证 Worker 已经加载了精确版本和 build digest，但它主要是在确认部署结果。

它还不能在同一个 Python Worker 内完成：

- 动态加载新 Extension generation；
- 让新旧两个版本同时存在；
- 把新请求切换到新版本；
- 等旧版本的在途调用结束；
- 自动卸载旧版本并释放资源。

因此需要准确区分：Agent、Skill、Scenario 和 Provider 配置可以动态发布，不代表 Python Extension 代码已经具备细粒度热替换。

### 3. 缺少 Extension 依赖图

JoyhouseBot 已经有精确的 `plugin_requirements`，但这仍然是一种版本约束，不是 reactive dependency。

我们还不能表达：

- Extension A 提供某个运行时服务；
- Extension B 只有在该服务存在时才能激活；
- A 被替换时只重新加载受影响的 B；
- A 卸载前必须等待 B 完成清理。

Workflow DAG 也不能承担这项职责。Workflow 描述业务执行，Extension 依赖图描述 Runtime 自身如何组成。

### 4. Connector generation 已有雏形，但缺少 drain

JoyhouseBot 已经可以先预热新 Connector generation，成功后再替换 Tool 目录。这是一个正确起点。

但为了避免破坏在途请求，旧 generation 目前会一直保留到 Worker 关闭。我们还缺少调用引用计数、draining 状态、自动关闭期限和隔离后的强制终止。

### 5. 异构 Worker 需要能力感知路由

在滚动升级期间，不同 Worker 可能加载不同 Extension build。当前 Runtime 会在执行前检查精确依赖，但 Worker claim 本身还没有完整使用这些依赖做资格过滤。

结果可能是错误 Worker 先取得 Run，再因为缺少精确 Plugin build 而失败。更正确的方式是：不合格 Worker 根本不能 claim 该 Run，让任务留给具备冻结 generation 的 Worker。

## 五、不应该直接照搬 Cordis

这篇论文很有价值，但也有明确边界。

- Runtime 仍然需要相信 Extension 作者提供的 inverse 是正确的；
- 恶意代码不能只靠语言级 Context 隔离，仍然需要子进程、容器或 Wasm；
- 外部业务写入不能自动回滚；
- 跨包接口版本和结构兼容仍是开放问题；
- 当前生产案例主要来自 Koishi 一个 TypeScript 生态；
- 论文中的 Cordis v4 与实际大规模使用的 Cordis v3 也不是完全相同版本；
- 自进化 Agent Harness 仍是未来验证方向，而不是已经完成的生产结论。

Python 也不像一个专门设计的组件语言那样容易真正卸载模块。简单删除 `sys.modules` 不能保证后台线程、闭包、全局对象和第三方库资源全部消失。

因此 JoyhouseBot 不应该假装实现“安全的 Python 模块热卸载”。对第三方 Extension，更可靠的长期方案是独立子进程或 Wasm generation；进程内加载只留给可信、边界清晰的官方组件。

## 六、JoyhouseBot 的改进方向：增加 Extension Generation Runtime

我倾向于保留现有 PostgreSQL-first 执行面，在 Worker 内新增一个明确的 Extension Generation Runtime。

```text
PostgreSQL desired state / immutable releases
                    ↓
        Extension Generation Manager
          ├─ generation A：draining
          └─ generation B：active
                    ↓
 ExtensionContext：资源登记、依赖、清理、调用计数
                    ↓
 Capability Router：按 Run snapshot 路由精确 generation
                    ↓
 Dispatcher：审批、幂等、业务写入与对账
```

这里需要坚持几个原则。

### 原则一：Run 永远绑定精确 generation

新版本激活以后，新 Run 使用新 generation；已经开始的 Run 继续使用冻结的旧 generation，不能在中途换实现。

### 原则二：先切流量，再 drain，最后卸载

新 generation 必须先完成加载、健康检查和必要 Eval。激活后，旧 generation 进入 draining，不再接受新调用；引用计数归零后，Runtime 才按相反顺序执行清理。

### 原则三：ExtensionContext 只能提供窄能力

Extension 不获得 RuntimeStore、数据库连接或任意 Service Locator。它只能通过 SDK 提供的 Context：

- 注册 Capability、Provider、Channel 或 Connector；
- 创建受管理的异步任务；
- 登记连接和订阅；
- 声明 provides/requires；
- 登记 disposer；
- 访问经过授权和拦截的 Runtime Port。

所有通过 Context 创建的资源都归属于一个明确 generation。

### 原则四：外部业务副作用继续走现有治理链

`dispose()` 只能回收 Runtime 边界内的资源，不能被用来伪装退款、邮件、发布和客户数据修改已经自动回滚。

这些操作仍然必须走 Action Intent、Approval、Idempotency、WriteReceipt 和 Reconciliation。

### 原则五：非可信 Extension 使用真正隔离

第三方代码应该运行在独立进程、容器或 Wasm 中，通过异步、版本化协议访问 Host 提供的能力。发生无法停止的任务或错误清理时，Runtime 可以终止整个 generation 的隔离边界。

## 七、建议的实施顺序

第一步，先解决正确性问题：让 Run claim 根据冻结的 Plugin requirements 过滤 Worker，并把 Extension 生命周期统一为 `loading → active → draining → inactive/failed`。

第二步，统一 Provider、Capability、Connector 和 Channel 的 generation 管理，增加 invocation 引用计数，避免旧客户端和连接只能等到 Worker 关闭才释放。

第三步，在 Extension SDK 中加入受控的 `ExtensionContext` 和 disposer 栈，让注册、后台任务、连接与事件订阅都可追踪、可审计、可逆。

第四步，为 Manifest 增加 namespaced `provides/requires`、接口版本和 schema digest，形成 Extension 依赖图，并实现 Consumer 先退出、Provider 后清理。

第五步，实现新旧 generation 并存、蓝绿切换、drain 和失败回滚。开发环境可以提供 HMR，但生产环境优先追求可解释的 generation rollout，而不是追求“保存文件立即生效”。

第六步，在这些机制稳定之后，再考虑自进化 Harness：Agent 可以提出新的 Extension 或配置，但生成物仍必须经过签名、静态检查、Eval、权限审查、灰度、人工确认和可撤销发布。Agent 不应直接修改正在承载自己的 Core 进程。

## 八、这对 JoyhouseBot 产品定位意味着什么

WordPress 的价值不只是能执行 PHP，而是建立了一套稳定的内容事实源、扩展契约、生命周期和生态分发机制。

同样，JoyhouseBot 如果希望成为 AI 时代的开放内核，不能只做到“Agent 可以调用很多 Tool”。它需要同时拥有：

- PostgreSQL-first 的长期执行事实源；
- 对真实业务副作用的治理与恢复；
- 可版本化、可验证、可撤销的 Agent 与 Skill；
- 可装载、可依赖、可 drain、可卸载的 Extension generation；
- 与 Core 解耦、可以独立售卖的 App；
- 让普通个人和中小团队也能使用的控制面。

这也进一步澄清了 JoyhouseBot 中几个概念的边界：

- Skill 是可复用的方法、规则和上下文组织，不拥有进程生命周期；
- Extension 是进入 Runtime 的技术能力，需要 generation、隔离和资源治理；
- App 是完整业务解决方案，可以拥有自己的数据模型、用户界面和商业模式；
- JoyhouseBot Core 负责让它们在统一的执行、权限、审计和恢复机制下协作。

## 结语

这篇论文给我的最大启发，不是 JoyhouseBot 应该变成 Cordis，而是一个成熟的 Agent Runtime 必须同时回答两个问题：

> Agent 的任务如何在进程死亡以后继续存在？

以及：

> 组成 Agent Runtime 的能力如何在进程仍然运行时安全变化？

JoyhouseBot 已经在回答第一个问题，也建立了外部业务副作用的治理基础。下一步应该补上第二个问题：把当前“能够发现和发布 Extension”的控制面，推进为真正“能够组合、切换、drain 和卸载 Extension”的运行面。

如果这一步完成，JoyhouseBot 就不只是一个长期任务执行器，而会更接近我们真正想建设的东西：

> 一个由用户掌控、可以长期运行，并能安全吸收新能力的 AI-native Runtime 内核。
