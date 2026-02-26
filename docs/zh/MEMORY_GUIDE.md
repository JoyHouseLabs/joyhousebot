# Memory 命令使用指南

本文档介绍 joyhousebot 的 Memory 命令，包括历史记录搜索和过期记忆清理。

## 概述

joyhousebot 提供两个 Memory 相关的子命令：

1. **`memory search`** - 搜索 agent 历史记录（HISTORY.md）
2. **`memory janitor`** - 扫描并归档过期的 P1/P2 记忆条目

---

## 一、memory search - 记忆搜索

### 1.1 功能说明

`memory search` 命令用于搜索指定 agent 的记忆文件，查找包含特定关键词的内容。该命令复用了 agent 内部的记忆搜索服务，确保 CLI 和 agent 搜索结果一致。

**搜索方式**：grep 风格（正则匹配，不区分大小写）

**搜索范围**：
- `memory/MEMORY.md` - 长期记忆
- `memory/HISTORY.md` - 对话历史
- `memory/.abstract` - 记忆摘要
- `memory/*.md` - 其他记忆文件（如日期归档）
- `memory/insights/*.md` - 洞察记录
- `memory/lessons/*.md` - 经验教训

### 1.2 命令语法

```bash
joyhousebot memory search <关键词> [选项]
```

### 1.3 参数与选项说明

| 参数/选项 | 默认值 | 说明 | 必填 |
|----------|--------|------|------|
| `<关键词>` | — | 要搜索的关键词或短语（支持正则表达式） | 是 |
| `--agent-id` | `""` | 指定要搜索的 agent ID（默认使用 joy agent） | 否 |
| `--workspace` | `""` | 覆盖 workspace 路径（优先级高于 --agent-id） | 否 |
| `--limit` | `20` | 最多显示匹配结果数 | 否 |
| `--scope-key` | `""` | 记忆隔离作用域（用于会话/用户隔离） | 否 |

### 1.4 Agent 工作区说明

每个 agent 都有独立的工作区和记忆目录：

| Agent ID | 名称 | 工作区路径 |
|----------|------|-----------|
| `joy` | JoyAgent | `~/.joyhousebot/agents/joy/memory/` |
| `programming` | 编程 | `~/.joyhousebot/agents/programming/memory/` |
| `finance` | 金融 | `~/.joyhousebot/agents/finance/memory/` |
| `education` | 教育 | `~/.joyhousebot/agents/education/memory/` |
| `growth` | 成长 | `~/.joyhousebot/agents/growth/memory/` |

### 1.5 使用示例

#### 示例 1：搜索默认 agent 的记忆

```bash
joyhousebot memory search python
```

输出示例：
```
Found 2 match(es):

memory/MEMORY.md:15
## Programming Notes

- Preferred Python version: 3.11+
- Use type hints for all new code

memory/HISTORY.md:42
2024-01-15 10:30:45 | USER | 如何用 Python 处理 CSV 文件？
```

#### 示例 2：搜索特定 agent 的记忆

```bash
# 搜索教育 agent 的记忆
joyhousebot memory search 数学 --agent-id education

# 搜索金融 agent 的记忆
joyhousebot memory search 股票 --agent-id finance

# 搜索编程 agent 的记忆
joyhousebot memory search docker --agent-id programming
```

#### 示例 3：限制匹配结果数

```bash
# 只显示前 5 条匹配结果
joyhousebot memory search hello --limit 5
```

#### 示例 4：指定自定义 workspace

```bash
# 使用自定义 workspace 路径
joyhousebot memory search test --workspace /path/to/custom/workspace
```

#### 示例 5：使用正则表达式搜索

```bash
# 搜索包含数字的行
joyhousebot memory search "\d{4}-\d{2}-\d{2}"

# 搜索包含"重要"或"关键"的记录
joyhousebot memory search "重要|关键"
```

#### 示例 6：搜索带作用域隔离的记忆

```bash
# 搜索特定会话的记忆
joyhousebot memory search hello --scope-key "session:abc123"
```

### 1.6 注意事项

- 搜索是**不区分大小写**的
- 支持正则表达式语法（如 `|` 表示或，`\d` 表示数字等）
- 搜索范围包括所有记忆文件（MEMORY.md、HISTORY.md、.abstract 等）
- 返回结果包含上下文内容，方便理解匹配位置
- 如果没有找到匹配项，会显示 "No matches."
- 如果记忆目录不存在，会提示路径并退出

### 1.7 常见错误

#### 错误 1：Agent ID 不存在

```bash
joyhousebot memory search test --agent-id unknown
```

错误输出：
```
ValueError: Agent 'unknown' not found. 
Available agents: joy, programming, finance, education, growth
```

**解决方法**：使用 `--help` 查看可用的 agent ID，或使用正确的 agent ID。

#### 错误 2：记忆目录不存在

```bash
joyhousebot memory search hello
```

错误输出：
```
Memory directory not found: /Users/joyhouse/.joyhousebot/workspace/memory
```

**解决方法**：
1. 指定正确的 agent ID：`joyhousebot memory search hello --agent-id joy`
2. 或者先与 agent 进行一些对话，生成记忆文件

---

## 二、memory janitor - 记忆清理

### 2.1 功能说明

`memory janitor` 命令用于扫描 agent 的 MEMORY.md 文件，查找过期的 P1/P2 记忆条目，并将它们归档到 `memory/archive/` 目录。

**用途**：
- 定期清理过期的短期记忆（P1、P2）
- 避免记忆文件过大影响性能
- 保留重要记忆的同时整理档案

### 2.2 命令语法

```bash
joyhousebot memory janitor [选项]
```

### 2.3 选项说明

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--dry-run` / `--run` | `--dry-run` | 预览模式（默认）或实际执行归档 |
| `--agent-id` | `""` | 指定要清理的 agent ID（默认使用 joy agent） |
| `--workspace` | `""` | 覆盖 workspace 路径（优先级高于 --agent-id） |

### 2.4 记忆优先级说明

MEMORY.md 中的记忆条目按优先级标记：

| 优先级 | 标记 | 说明 | 过期时间 |
|--------|------|------|----------|
| P0 | `[P0]` | 长期记忆，永久保留 | 不过期 |
| P1 | `[P1]` | 短期记忆 | 通常 7-30 天过期 |
| P2 | `[P2]` | 临时记忆 | 通常 1-7 天过期 |

**注意**：只有过期的 P1 和 P2 记忆会被归档，P0 记忆永远不会被清理。

### 2.5 使用示例

#### 示例 1：预览将要归档的记忆（推荐）

```bash
# 预览默认 agent 的归档计划
joyhousebot memory janitor

# 预览特定 agent 的归档计划
joyhousebot memory janitor --agent-id education
```

输出示例（预览模式）：
```
[d]Would archive 3 entries (use --run to execute):
  [2024-01-10] [P2] 临时笔记：会议时间下午3点
  [2024-01-08] [P1] 项目截止日期是下周三
  [2024-01-05] [P2] 待办事项：更新文档
```

#### 示例 2：实际执行归档

```bash
# 先预览
joyhousebot memory janitor --dry-run

# 确认无误后，执行归档
joyhousebot memory janitor --run
```

输出示例（执行模式）：
```
[green]Archived 3 entries.[/green]
  [2024-01-10] [P2] 临时笔记：会议时间下午3点
  [2024-01-08] [P1] 项目截止日期是下周三
  [2024-01-05] [P2] 待办事项：更新文档
```

#### 示例 3：清理特定 agent 的记忆

```bash
# 预览编程 agent 的归档计划
joyhousebot memory janitor --agent-id programming --dry-run

# 执行归档
joyhousebot memory janitor --agent-id programming --run
```

#### 示例 4：指定自定义 workspace

```bash
# 预览自定义 workspace 的归档计划
joyhousebot memory janitor --workspace /path/to/custom/workspace --dry-run

# 执行归档
joyhousebot memory janitor --workspace /path/to/custom/workspace --run
```

### 2.6 归档文件位置

归档后的记忆条目会保存在 agent 的 `memory/archive/` 目录下：

```
~/.joyhousebot/agents/<agent_id>/memory/archive/
```

归档文件命名格式：
- `archive_YYYY-MM-DD_HH-mm-ss.md` - 按归档时间命名
- 每次归档操作会创建一个新的归档文件

### 2.7 注意事项

- **默认是预览模式**：必须使用 `--run` 才会实际执行归档
- **只归档过期条目**：只有已过期的 P1/P2 记忆会被处理
- **P0 记忆不受影响**：长期记忆（P0）永远不会被归档
- **建议定期执行**：建议每周或每月运行一次清理，保持记忆文件整洁

---

## 三、典型使用场景

### 场景 1：查找之前的对话内容

```bash
# 查找之前关于某个话题的对话
joyhousebot memory search 机器学习

# 查找某个时间段的对话（通过关键词）
joyhousebot memory search 2024-01
```

### 场景 2：跨 agent 搜索

```bash
# 在不同 agent 中搜索相同的关键词
joyhousebot memory search python --agent-id programming
joyhousebot memory search python --agent-id education
joyhousebot memory search python --agent-id joy
```

### 场景 3：定期清理记忆

```bash
# 每周清理一次过期记忆（脚本示例）

#!/bin/bash

# 清理所有 agent 的过期记忆
for agent in joy programming finance education growth; do
    echo "清理 agent: $agent"
    joyhousebot memory janitor --agent-id $agent --run
done
```

### 场景 4：搜索后清理相关记忆

```bash
# 1. 先搜索某个话题的所有记录
joyhousebot memory search 项目A

# 2. 如果发现有很多过期的临时笔记，清理它们
joyhousebot memory janitor --agent-id joy --run
```

---

## 四、故障排查

### Q: 搜索返回 "No matches."

**可能原因**：
1. 关键词拼写错误
2. HISTORY.md 文件中没有该关键词
3. 指定了错误的 agent

**解决方法**：
```bash
# 1. 检查关键词拼写
joyhousebot memory search python

# 2. 尝试更通用的关键词
joyhousebot memory search code

# 3. 检查可用的 agent
joyhousebot config agents list

# 4. 尝试搜索其他 agent
joyhousebot memory search python --agent-id programming
```

### Q: janitor 返回 "No expired P1/P2 entries."

**说明**：这不是错误，说明当前没有过期的记忆需要归档。

**可能原因**：
1. 记忆都是长期记忆（P0）
2. P1/P2 记忆都还没过期
3. MEMORY.md 文件为空

**解决方法**：
```bash
# 1. 查看 MEMORY.md 内容
cat ~/.joyhousebot/agents/joy/memory/MEMORY.md

# 2. 检查记忆的优先级和过期时间
# P1 记忆通常 7-30 天过期，P2 记忆 1-7 天过期
```

### Q: 如何恢复已归档的记忆？

**解决方法**：
```bash
# 1. 查看归档文件
ls -lh ~/.joyhousebot/agents/joy/memory/archive/

# 2. 查看归档内容
cat ~/.joyhousebot/agents/joy/memory/archive/archive_2024-01-15_10-30-00.md

# 3. 如需恢复，手动复制回 MEMORY.md
# 注意：恢复时要确保不会造成重复
```

### Q: janitor 归档了不该归档的记忆？

**预防措施**：
```bash
# 1. 总是先用 --dry-run 预览
joyhousebot memory janitor --dry-run

# 2. 仔细检查即将归档的内容

# 3. 确认无误后才执行 --run
joyhousebot memory janitor --run
```

**恢复方法**：
如果误归档了重要记忆，可以从 archive 目录手动恢复：
```bash
# 查看归档文件
cat ~/.joyhousebot/agents/joy/memory/archive/archive_*.md

# 将需要的记忆复制回 MEMORY.md
```

---

## 五、最佳实践

1. **定期清理记忆**：建议每周或每月运行一次 janitor，保持记忆文件整洁

2. **使用预览模式**：执行归档前总是先用 `--dry-run` 预览，避免误删重要记忆

3. **合理设置记忆优先级**：
   - 重要信息标记为 P0（长期记忆）
   - 中期信息标记为 P1
   - 临时信息标记为 P2

4. **使用有意义的搜索关键词**：搜索时使用准确、具体的关键词，提高命中率

5. **跨 agent 搜索**：如果不确定某个话题是在哪个 agent 讨论的，可以逐个搜索

6. **定期备份归档文件**：虽然归档文件已经保存，但定期备份到安全位置是个好习惯

---

## 六、相关命令

以下命令可能与 Memory 命令配合使用：

```bash
# 查看 agent 列表和配置
joyhousebot config agents list

# 与 agent 对话（会记录到 HISTORY.md）
joyhousebot agent --message "帮我记录一个事项"

# 查看 agent 工作区
joyhousebot directory agents

# 查看会话历史
joyhousebot sessions list
joyhousebot sessions history <会话ID>

# 查看配置
joyhousebot config get
```

---

## 七、知识库 Pipeline

### 7.1 概述

知识库 pipeline 是一个独立子进程，用于监控 agent 的知识库目录，自动转换文档并建立索引。

**工作流程**：
```
knowledgebase/ → processed/*.md → FTS5 索引
     ↓              ↓               ↓
  文件监控      格式转换       全文搜索
```

### 7.2 架构设计

每个配置了知识库的 agent 都会启动一个独立的 pipeline 子进程：

| 组件 | 职责 |
|------|------|
| `pipeline_worker.py` | 独立子进程入口，处理环境变量和启动监控 |
| `watcher.py` | 文件监控线程，监听 `knowledgebase/` 目录变化 |
| `pipeline_queue.py` | 任务队列，异步处理文件转换和索引 |
| `converter.py` | 文档转换器（PDF、TXT、Markdown → 统一 Markdown） |
| `indexer.py` | FTS5 索引器，将转换后的文档索引到 SQLite |

**进程结构**：
```
主进程
├── Agent: education → 子进程 1 → 监控 ~/.joyhousebot/agents/education/knowledgebase
├── Agent: growth     → 子进程 2 → 监控 ~/.joyhousebot/agents/growth/knowledgebase
└── Agent: programming → 子进程 3 → 监控 ~/.joyhousebot/agents/programming/knowledgebase
```

### 7.3 支持的文件格式

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| Markdown | `.md` | 直接使用 |
| 文本文件 | `.txt` | 直接使用 |
| PDF | `.pdf` | 使用 PyPDF2 解析文本 |
| 其他 | - | 不支持，会被自动跳过 |

### 7.4 配置说明

在 `config.json` 中配置知识库 pipeline：

```json
{
  "tools": {
    "knowledge_pipeline": {
      "knowledge_source_dir": "knowledgebase",
      "knowledge_processed_dir": "knowledge/processed",
      "watch_enabled": true,
      "subprocess_enabled": true
    }
  }
}
```

**配置项**：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `knowledge_source_dir` | `knowledgebase` | 源文档目录 |
| `knowledge_processed_dir` | `knowledge/processed` | 处理后文档目录 |
| `watch_enabled` | `true` | 是否启用文件监控 |
| `subprocess_enabled` | `true` | 是否使用独立子进程 |

### 7.5 使用示例

#### 示例 1：添加文档到知识库

```bash
# 将文档复制到 agent 的 knowledgebase 目录
cp ~/Documents/python-guide.pdf ~/.joyhousebot/agents/programming/knowledgebase/

# pipeline 子进程会自动：
# 1. 检测到新文件
# 2. 转换为 Markdown（processed/python-guide.md）
# 3. 建立 FTS5 索引
```

#### 示例 2：手动触发索引

```bash
# 如果文件监控未启用，可以手动触发索引
joyhousebot knowledge sync --agent-id programming
```

#### 示例 3：查看索引状态

```bash
# 查看已索引的文档
joyhousebot knowledge list --agent-id programming
```

### 7.6 故障排查

**Q: 文件监控不工作？**

```bash
# 检查子进程是否运行
ps aux | grep pipeline_worker

# 查看日志
tail -f ~/.joyhousebot/agents/programming/logs/pipeline.log
```

**Q: 文档转换失败？**

- 检查文件格式是否支持
- 检查文件权限
- 查看 pipeline 日志中的错误信息

**Q: 搜索不到知识库内容？**

```bash
# 检查索引是否存在
ls ~/.joyhousebot/agents/programming/knowledge/processed/

# 重新同步索引
joyhousebot knowledge sync --agent-id programming --force
```

---

## 八、相关文档

- [CLI 参考](CLI_REFERENCE.md) - 完整的命令与参数说明
- [设计与架构](DESIGN_AND_ARCHITECTURE.md) - 分层设计、核心组件、记忆系统、知识库 pipeline
- [Agent 自主决策配置](AGENT_AUTONOMOUS_DECISION.md) - 如何配置 agent 自主决策
- [Memory vs OpenClaw](MEMORY_VS_OPENCLAW.md) - Memory 系统与 OpenClaw 的对比

---

