# 记忆系统：joyhousebot 与 OpenClaw 对比

本文基于实际代码实现对比 joyhousebot 与 [OpenClaw](https://docs.openclaw.ai/concepts/memory) 的记忆设计。

## 一、整体定位

| 维度 | OpenClaw | joyhousebot |
|------|----------|-------------|
| **设计哲学** | Markdown 为单一事实来源；索引可随时从文件重建 | 文件优先、Markdown 为源；索引/检索可插拔 |
| **默认工具** | `memory_get` + `memory_search`（语义检索） | `memory_get` + `retrieve(scope=memory)`（内置 grep，可选 MCP/SQLite 向量） |
| **记忆与知识库** | 记忆即 workspace 内 MEMORY + daily；无单独「知识库」层 | **记忆**（memory/）与**知识库**（knowledgebase → processed → FTS5）分离 |

---

## 二、文件与层级

### OpenClaw

- **两层**：
  - `MEMORY.md`：长期、人工整理，仅在私有会话加载
  - `memory/YYYY-MM-DD.md`：按日追加的日报，会话启动时加载「今天 + 昨天」
- 无 L0/L1/L2/insights/lessons 等显式层级；无 P0/P1/P2 过期标签

### joyhousebot

- **多层 + 长期 + 归档**：
  - **L0**：`memory/.abstract`，目录索引（约 100–300 tokens），检索入口
  - **L1**：`memory/insights/*.md`、`memory/lessons/*.md`，由 compaction 从 L2 归纳
  - **L2**：`memory/YYYY-MM-DD.md`（日报）、`memory/HISTORY.md`（追加历史）
  - **长期**：`MEMORY.md`，支持 **P0/P1/P2** 标签，P1/P2 可带 `[P1|expire:YYYY-MM-DD]`
  - **archive/**：janitor 将过期 P1/P2 移入，不删内容
- **特点**：L0/L1 由定时 compaction（LLM 归纳）生成；MEMORY 可有时效性（P1/P2 过期归档）

**实现位置**：[memory.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory.py#L32-36) 定义 MemoryStore 类，管理所有文件结构

---

## 三、检索与搜索

### OpenClaw

- **默认**：SQLite 索引（FTS + 可选向量），`memory_search` 为**语义检索**（chunk ~400 token，overlap 80）
- **可选**：`memory.backend = "qmd"` → QMD 侧车（BM25 + 向量 + rerank，本地优先）
- **混合策略**：BM25 + 向量加权合并；可配置权重、MMR、时间衰减等
- **配置**：`agents.defaults.memorySearch`（provider、model、remote、batch、sync.watch 等）

### joyhousebot

- **默认**：**内置 grep**：对 MEMORY.md、HISTORY.md、.abstract、YYYY-MM-DD.md、insights/*、lessons/* 做关键词匹配（无向量）
- **语义后端**（与 OpenClaw 对齐）：
  - **builtin**（默认）：仅 grep，零依赖
  - **mcp_qmd**：通过 MCP 调用 QMD，语义检索
  - **sqlite_vector**：SQLite + embedding 索引（MEMORY + memory/*.md 等），语义检索；需配置 `embedding_provider` / `embedding_model`
  - **auto**：先尝试 mcp_qmd → 再 sqlite_vector → 最后回退 builtin
- **可选**：`memory_vector_enabled = true`：在 **builtin** 路径下对 grep 结果做 embedding 重排（不影响 mcp_qmd / sqlite_vector）
- **配置**：`tools.retrieval.memory_backend`、`memory_top_k`、`memory_vector_enabled`

**实现位置**：
- [adapter.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/adapter.py#L26-103)：统一检索入口，支持 scope=knowledge/memory
- [memory_search.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/memory_search.py)：builtin grep 实现
- [memory_vector_store.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/memory_vector_store.py)：sqlite_vector 后端

---

## 四、会话/用户隔离（scope）

### OpenClaw

- 文档强调「仅私有会话加载 MEMORY」；通过 `scope` / `session.sendPolicy` 等控制谁可搜到记忆
- QMD 的 `scope`、`match.keyPrefix` 等控制检索可见范围

### joyhousebot

- **显式 scope 配置**：`tools.retrieval.memory_scope` = `shared` | `session` | `user`
  - **shared**：单一 `memory/`，所有人共享
  - **session**：`memory/<session_key>/`，按会话隔离
  - **user**：`memory/<user_id>/`，按用户隔离（`memory_user_id_from`、`memory_user_id_metadata_key`）
- **检索与上下文**：`retrieve(scope=memory)` 和 [ContextBuilder](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/context.py#L104-132) 的 system 记忆块都会按当前 session/user 解析 scope_key，只读对应子目录
- **特点**：多租户/多会话下可严格隔离记忆，且 janitor、compaction 支持按 scope 执行

**实现位置**：
- [memory.py#safe_scope_key](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory.py#L19-23)：规范化 scope_key 为安全目录名
- [memory.py#MemoryStore.__init__](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory.py#L38-46)：根据 scope_key 创建隔离目录

---

## 五、Compaction 与 Flush

### OpenClaw

- **Compaction**：会话上下文接近上限时做压缩（见 Session management + compaction）
- **Memory flush**：压缩前**一次静默 agent 轮**，提醒模型把持久记忆写盘（`compaction.memoryFlush`），默认提示含 NO_REPLY，用户无感

### joyhousebot

- **Compaction**（定时/调度）：
  - **L2 → L1**：旧日报（`older_than_days` 之前的）+ HISTORY 段落 → LLM 归纳 → 写入 `insights/*.md`、`lessons/*.md`
  - **L1 + MEMORY → L0**：用 MEMORY 与近期 insights/lessons 生成/更新 `.abstract`
- **Memory flush**：`memory_flush_before_consolidation` 为 true 时，在 compaction 前可做**一次 LLM 调用**，产出 JSON（`daily_log_entry`、`memory_additions`），再写入日报/MEMORY
- **特点**：有显式 L0/L1 归纳流水线；flush 与 OpenClaw 思路一致但由配置开关控制

**实现位置**：
- [memory_compaction.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory_compaction.py#L199-249)：`run_memory_compaction` 函数支持按 scope 运行
- [memory_compaction.py#_run_memory_compaction_one](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory_compaction.py#L30-196)：单个 scope 的 L2→L1→L0 流水线

---

## 六、Janitor（过期归档）

### OpenClaw

- 无内置 janitor 机制

### joyhousebot

- **Janitor**：扫描 MEMORY.md 中的 `[P1|expire:YYYY-MM-DD]` 和 `[P2|expire:YYYY-MM-DD]` 标签
- **归档**：将过期条目移至 `archive/expired-YYYY-MM-DD.md`，原 MEMORY.md 中删除
- **支持 scope**：当 `memory_scope` 为 session/user 时，为每个 scope 运行 janitor
- **特点**：记忆可带时效性，过期自动归档而非删除

**实现位置**：
- [memory_janitor.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory_janitor.py#L95-132)：`run_janitor` 函数支持按 scope 运行
- [memory_janitor.py#_parse_expiry_lines](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory_janitor.py#L19-33)：解析过期标签

---

## 七、知识库检索

### OpenClaw

- 无独立「知识库」概念，所有文档通过 memory_search 检索

### joyhousebot

- **知识库**：`knowledgebase/` → `knowledge/processed/` → FTS5 索引
- **检索方式**：`retrieve(scope=knowledge)` 调用混合检索
  - **FTS5**：全量文本搜索（SQLite FTS5）
  - **Vector**：可选向量检索（Chroma/Qdrant/pgvector），通过 RRF 融合
- **与记忆分离**：`retrieve` 用 `scope=knowledge` / `scope=memory` 区分检索来源

**知识库 Pipeline**：
- **独立子进程**：每个配置了知识库的 agent 都会启动一个独立的 pipeline 子进程
- **自动监控**：使用 watchfiles 监控 `knowledgebase/` 目录变化
- **自动转换**：PDF、TXT、Markdown → 统一 Markdown 格式
- **自动索引**：将转换后的文档索引到 FTS5 数据库

**实现位置**：
- [hybrid.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/hybrid.py)：FTS5 + 向量混合检索，RRF 融合
- [store.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/store.py)：FTS5 索引实现
- [pipeline_worker.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/knowledge_pipeline/pipeline_worker.py)：独立子进程入口
- [watcher.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/knowledge_pipeline/watcher.py)：文件监控
- [pipeline_queue.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/knowledge_pipeline/pipeline_queue.py)：任务队列
- [converter.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/knowledge_pipeline/converter.py)：文档转换
- [indexer.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/knowledge_pipeline/indexer.py)：FTS5 索引
- [service.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/knowledge_pipeline/service.py)：子进程管理

---

## 八、配置对照

| 能力 | OpenClaw | joyhousebot |
|------|----------|-------------|
| 今日/昨日日报入 context | 会话启动加载 today + yesterday | `memory_include_daily_in_context`（默认 true，OpenClaw-aligned） |
| 记忆优先提示 | — | `memory_first`：提示先查 memory 再查 knowledge |
| 使用 L0 索引 | — | `memory_use_l0`：system 里注入 L0 + MEMORY |
| HISTORY 条数上限 | — | `history_max_entries`：只保留最近 N 条 |
| 压缩前 flush | `compaction.memoryFlush` | `memory_flush_before_consolidation` + 两条 prompt |
| 记忆检索后端 | 内置 SQLite / QMD | `memory_backend`：builtin / mcp_qmd / sqlite_vector / auto |
| 记忆向量重排 | 内置混合检索 | `memory_vector_enabled`：对 builtin 结果做 embedding 重排 |
| 知识库检索后端 | — | `knowledge_backend`：builtin / qmd / auto |
| 记忆隔离 | 通过 session.sendPolicy | `memory_scope`：shared / session / user |
| 记忆过期归档 | — | Janitor 自动归档过期 P1/P2 到 archive/ |

**配置位置**：[schema.py#RetrievalConfig](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/config/schema.py#L402-430)

---

## 九、差异小结

### joyhousebot 相对 OpenClaw 的特点

1. **L0/L1/L2 + 归档**：多一层「索引 + 归纳」结构，compaction 产出 insights/lessons 并更新 .abstract；MEMORY 支持 P0/P1/P2 与过期归档。
2. **记忆与知识库分离**：知识库走 knowledgebase → processed → FTS5（及可选向量），与 memory 检索分离；`retrieve` 用 `scope=knowledge` / `scope=memory` 区分。
3. **默认检索更轻**：内置为 grep，不依赖向量或 QMD；要语义检索需配 MCP 或 `memory_vector_enabled`。
4. **scope 可配置**：shared/session/user 与目录一一对应，检索、上下文、janitor、compaction 都按 scope 执行。
5. **Janitor 自动归档**：支持 P1/P2 过期标签，自动归档到 archive/ 而非删除。
6. **OpenClaw 兼容点**：`memory_get` 行为对齐（缺失文件返回空文本）；今日/昨日入 context、flush 前写入、memory_vector 重排等配置与 OpenClaw 对齐或留扩展位。

### 若从 OpenClaw 迁到 joyhousebot

- **MEMORY.md + memory/YYYY-MM-DD.md** 可直接复用（路径与语义一致）。
- **memory_search 语义检索**：需在 joyhousebot 侧配置 `memory_backend = mcp_qmd`（或 auto）并接入 QMD，或开启 `memory_vector_enabled` 做重排。
- **无 L0/L1**：不启用 `memory_use_l0`、不跑 compaction 即可；L1/L0 为可选增强。
- **无 P1/P2**：MEMORY 不写 `[P1|expire:...]` 即可；janitor 仅处理带过期标签的行。

---

*文档基于代码实现整理：*
- [memory.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory.py)
- [memory_compaction.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory_compaction.py)
- [memory_janitor.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/memory_janitor.py)
- [adapter.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/adapter.py)
- [memory_search.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/memory_search.py)
- [memory_vector_store.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/services/retrieval/memory_vector_store.py)
- [context.py](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/agent/context.py)
- [schema.py#RetrievalConfig](file:///Users/joyhouse/ZCodeProject/joyhouse/joyhousebot/joyhousebot/config/schema.py#L402-430)
