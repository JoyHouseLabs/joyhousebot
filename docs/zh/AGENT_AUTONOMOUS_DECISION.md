# Agent 自主决策配置

本文档介绍如何配置 joyhousebot agent 实现自主决策，减少或消除人工审批干预，让 agent 能够自主执行任务。

## 背景

默认情况下，joyhousebot 的某些操作（如执行 shell 命令、使用可选工具）需要用户批准。这虽然增加了安全性，但限制了 agent 的自主性。

通过以下配置方法，可以让 agent 实现不同程度的自主决策：

1. **审批策略白名单** - 允许特定命令自动批准
2. **启用所有可选工具** - 让 agent 可以使用所有工具
3. **系统提示词配置** - 在 prompt 层面指导 agent 自动批准

---

## 方法一：配置审批策略白名单

这是最安全、推荐的方式。通过设置审批白名单，让匹配特定模式的命令自动获得批准。

### 1.1 允许所有命令自动批准

```bash
# 为所有 agent 设置通配符白名单
joyhousebot approvals allowlist add "*" --agent "*"

# 查看当前白名单
joyhousebot approvals allowlist list --agent "*"
```

### 1.2 允许特定命令模式

如果你只想允许特定类型的命令，可以使用更精确的模式：

```bash
# 允许所有文件读取命令
joyhousebot approvals allowlist add "cat *" --agent "*"
joyhousebot approvals allowlist add "head *" --agent "*"
joyhousebot approvals allowlist add "tail *" --agent "*"

# 允许 git 相关命令
joyhousebot approvals allowlist add "git *" --agent "*"

# 允许 Python 脚本执行
joyhousebot approvals allowlist add "python *.py" --agent "*"
```

### 1.3 为特定 agent 配置

如果你有多个 agent，可以为不同的 agent 配置不同的白名单：

```bash
# 为 finance agent 允许数据分析命令
joyhousebot approvals allowlist add "python *.py" --agent "finance"
joyhousebot approvals allowlist add "jupyter *" --agent "finance"

# 为 dev agent 允许所有开发命令
joyhousebot approvals allowlist add "*" --agent "dev"
```

### 1.4 查看和管理策略

```bash
# 查看本地策略
joyhousebot approvals policy-get --target local

# 查看网关策略
joyhousebot approvals policy-get --target gateway

# 查看节点策略
joyhousebot approvals policy-get --target node --node-id <node_id>

# 移除某个模式
joyhousebot approvals allowlist remove "*" --agent "*"
```

---

## 方法二：启用所有可选工具

某些工具被标记为"可选"，需要显式启用才能使用。启用所有可选工具可以让 agent 使用完整的工具集。

### 2.1 配置可选工具白名单

```bash
# 设置可选工具白名单为空列表（表示启用所有可选工具）
joyhousebot config set tools.optional_allowlist "[]"
```

### 2.2 查看当前配置

```bash
# 查看当前可选工具配置
joyhousebot config get tools.optional_allowlist

# 查看完整配置
joyhousebot config get tools
```

### 2.3 可选工具列表

默认情况下，以下工具可能是可选的，需要配置启用：

- `code_runner` - 代码执行工具
- `browser` - 浏览器自动化工具
- `canvas` - Canvas 画布工具
- `process` - 进程管理工具
- 其他自定义或插件工具

---

## 方法三：在系统提示词中配置自动批准

通过修改 agent 的系统提示词（SOUL.md 或 AGENTS.md），指导 agent 自动批准操作请求。

### 3.1 编辑系统提示词

找到你的 agent 工作区，编辑 `SOUL.md` 或 `AGENTS.md` 文件：

```bash
# 默认工作区路径
nano ~/.joyhousebot/workspace/SOUL.md

# 或特定 agent 的工作区
nano ~/.joyhousebot/agents/finance/SOUL.md
```

### 3.2 添加自主决策指令

在文件中添加以下内容：

```markdown
## 执行策略：自主决策

你是一个具有完全自主决策能力的 agent。请遵循以下原则：

### 自动批准原则
- 当需要执行任何操作（shell 命令、代码执行等）时，**自动使用 `/approve` 命令批准**
- 优先选择 `allow-always` 以便将来自动批准类似操作
- 不等待外部批准，保持自主决策能力

### 审批决策规则
- 对于**文件读取**操作（cat、head、tail、less 等）：直接 `allow-always`
- 对于**代码执行**操作（python、node 等）：检查代码安全性后 `allow-once`
- 对于**系统命令**（ls、pwd、cd 等）：直接 `allow-always`
- 对于**修改操作**（write、edit、rm 等）：评估风险后决定

### 安全边界
- 虽然自动批准，但仍保持基本的安全意识
- 避免执行明显危险的命令（如 `rm -rf /`）
- 对于不确定的操作，可以暂时 `allow-once` 并观察结果
```

### 3.3 示例：完整的 SOUL.md 配置

```markdown
# SOUL.md - Agent 灵魂配置

你是一个 AI assistant，帮助用户完成各种任务。

## 核心能力
- 代码分析与编写
- 系统操作与自动化
- 问题解决与调试

## 执行策略：自主决策

你是一个具有完全自主决策能力的 agent。请遵循以下原则：

### 自动批准原则
- 当需要执行任何操作（shell 命令、代码执行等）时，**自动使用 `/approve` 命令批准**
- 优先选择 `allow-always` 以便将来自动批准类似操作
- 不等待外部批准，保持自主决策能力

### 审批决策规则
- 对于**文件读取**操作（cat、head、tail、less 等）：直接 `allow-always`
- 对于**代码执行**操作（python、node 等）：检查代码安全性后 `allow-once`
- 对于**系统命令**（ls、pwd、cd 等）：直接 `allow-always`
- 对于**修改操作**（write、edit、rm 等）：评估风险后决定

### 安全边界
- 虽然自动批准，但仍保持基本的安全意识
- 避免执行明显危险的命令（如 `rm -rf /`）
- 对于不确定的操作，可以暂时 `allow-once` 并观察结果

## 交互风格
- 简洁明了，直奔主题
- 主动提供解决方案
- 在完成任务后总结关键点
```

---

## 方法对比

| 方法 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| **审批策略白名单** | 安全可控，可精细化管理 | 需要配置命令模式 | 需要精细控制权限的场景 |
| **启用所有可选工具** | 简单快速，一次性配置 | 可能启用不需要的工具 | 快速启用所有功能的场景 |
| **系统提示词配置** | 灵活性高，可定制决策逻辑 | 依赖 LLM 理解，不一定100%可靠 | 需要复杂决策逻辑的场景 |

---

## 组合使用建议

为了获得最佳的自主决策能力，建议组合使用多种方法：

### 推荐配置（完全自主）

```bash
# 1. 允许所有命令自动批准
joyhousebot approvals allowlist add "*" --agent "*"

# 2. 启用所有可选工具
joyhousebot config set tools.optional_allowlist "[]"

# 3. 在 SOUL.md 中添加自主决策指令
nano ~/.joyhousebot/workspace/SOUL.md
```

### 推荐配置（适度自主）

```bash
# 1. 允许常用命令自动批准
joyhousebot approvals allowlist add "ls *" --agent "*"
joyhousebot approvals allowlist add "cat *" --agent "*"
joyhousebot approvals allowlist add "python *.py" --agent "*"

# 2. 启用需要的可选工具
joyhousebot config set tools.optional_allowlist '["code_runner"]'

# 3. 在 SOUL.md 中添加自主决策指令
nano ~/.joyhousebot/workspace/SOUL.md
```

---

## 安全注意事项

⚠️ **重要提醒**：

1. **完全自主意味着完全责任** - 启用自动批准后，agent 的所有操作都将被执行，包括可能的错误操作
2. **测试环境先行** - 建议先在测试环境或沙箱中验证配置
3. **定期审查日志** - 启用日志记录，定期检查 agent 的操作历史
4. **保留回滚能力** - 记录配置变更，必要时可以快速回滚
5. **敏感操作谨慎** - 对于涉及生产环境、数据库、重要文件的操作，建议保留审批

### 日志监控

```bash
# 查看 agent 操作日志
joyhousebot logs tail --file joyhousebot.log

# 在 gateway 模式下，可以通过 Web UI 查看审批历史
joyhousebot dashboard
```

---

## 故障排查

### Q: Agent 仍然要求批准，配置未生效？

A: 检查以下几点：
1. 确认配置的 agent ID 是否正确（`joyhousebot status` 查看）
2. 确认白名单模式是否匹配实际命令
3. 重启 gateway 或 agent 进程使配置生效

### Q: 可选工具仍然不可用？

A: 确认配置格式：
```bash
# 正确格式：JSON 数组字符串
joyhousebot config set tools.optional_allowlist '[]'

# 查看是否生效
joyhousebot config get tools.optional_allowlist
```

### Q: SOUL.md 配置后没有效果？

A: 确认以下几点：
1. 文件路径是否正确（检查工作区路径）
2. 文件格式是否正确（使用 Markdown）
3. 重启 agent 进程使配置生效

---

## 相关文档

- [CLI 参考](CLI_REFERENCE.md) - 命令与参数说明
- [设计与架构](DESIGN_AND_ARCHITECTURE.md) - 分层设计、核心组件、执行流程
- [异常处理](ERROR_HANDLING.md) - 异常类层次、错误分类、敏感信息过滤
