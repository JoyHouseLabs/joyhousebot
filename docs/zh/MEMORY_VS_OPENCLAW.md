# 记忆系统：joyhousebot 与 OpenClaw 对比

本文对比 joyhousebot 与 [OpenClaw](https://docs.openclaw.ai/concepts/memory) 的记忆设计，便于迁移与选型。

## 一、整体定位

| 维度 | OpenClaw | joyhousebot |
|------|----------|-------------|
| **设计哲学** | Markdown 为单一事实来源；索引可随时从文件重建 | 同：文件优先、Markdown 为源；索引/检索可插拔 |
| **默认工具** | `memory_get` + `memory_search`（语义检索） | `memory_get` + `retrieve(scope=memory)`（内置 grep，可选 MCP/qmd 语义） |
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
  - **L0**：`memory/.abstract`，目录索引（~100–300 tokens），检索入口
  - **L1**：`memory/insights/*.md`、`memory/lessons/*.md`，由 compaction 从 L2 归纳
  - **L2**：`memory/YYYY-MM-DD.md`（日报）、`memory/HISTORY.md`（追加历史）
  - **长期**：`MEMORY.md`，支持 **P0/P1/P2** 标签，P1/P2 可带 `[P1|expire:YYYY-MM-DD]`
  - **archive/**：janitor 将过期 P1/P2 移入，不删内容
- **特点**：L0/L1 由定时 compaction（LLM 归纳）生成；MEMORY 可有时效性（P1/P2 过期归档）

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
- **特点**：未配置则用 grep；配置后可选 QMD 或 SQLite 向量做语义回忆

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
- **检索与上下文**：`retrieve(scope=memory)` 和 `ContextBuilder` 的 system 记忆块都会按当前 session/user 解析 scope_key，只读对应子目录
- **特点**：多租户/多会话下可严格隔离记忆，且 janitor、compaction 支持按 scope 执行

---

## 五、 compaction 与 flush

### OpenClaw

- **Compaction**：会话上下文接近上限时做压缩（见 Session management + compaction）
- **Memory flush**：压缩前**一次静默 agent 轮**，提醒模型把持久记忆写盘（`compaction.memoryFlush`），默认提示含 NO_REPLY，用户无感

### joyhousebot

- **Compaction**（定时/调度）：
  - **L2 → L1**：旧日报 + HISTORY 段落 → LLM 归纳 → 写入 `insights/*.md`、`lessons/*.md`
  - **L1 + MEMORY → L0**：用 MEMORY 与近期 insights/lessons 生成/更新 `.abstract`
- **Memory flush**：`memory_flush_before_consolidation` 为 true 时，在 compaction 前可做**一次 LLM 调用**，产出 JSON（如 `daily_log_entry`、`memory_additions`），再写入日报/MEMORY
- **特点**：有显式 L0/L1 归纳流水线；flush 与 OpenClaw 思路一致但由配置开关控制

---

## 六、配置对照（记忆相关）

| 能力 | OpenClaw | joyhousebot |
|------|----------|-------------|
| 今日/昨日日报入 context | 会话启动加载 today + yesterday | `memory_include_daily_in_context`（默认 true，OpenClaw-aligned） |
| 记忆优先提示 | — | `memory_first`：提示先查 memory 再查 knowledge |
| 使用 L0 索引 | — | `memory_use_l0`：system 里注入 L0 + MEMORY |
| HISTORY 条数上限 | — | `history_max_entries`：只保留最近 N 条 |
| 压缩前 flush | `compaction.memoryFlush` | `memory_flush_before_consolidation` + 两条 prompt |
| 记忆检索后端 | 内置 SQLite / QMD | `memory_backend`：builtin / mcp_qmd / auto |
| 记忆向量重排 | 内置混合检索 | `memory_vector_enabled`：对 builtin 结果做 embedding 重排 |

---

## 七、差异小结

### joyhousebot 相对 OpenClaw 的特点

1. **L0/L1/L2 + 归档**：多一层「索引 + 归纳」结构，compaction 产出 insights/lessons 并更新 .abstract；MEMORY 支持 P0/P1/P2 与过期归档。
2. **记忆与知识库分离**：知识库走 knowledgebase → processed → FTS5（及可选向量），与 memory 检索分离；`retrieve` 用 `scope=knowledge` / `scope=memory` 区分。
3. **默认检索更轻**：内置为 grep，不依赖向量或 QMD；要语义检索需配 MCP 或 `memory_vector_enabled`。
4. **scope 可配置**：shared/session/user 与目录一一对应，检索、上下文、janitor、compaction 都按 scope 执行。
5. **OpenClaw 兼容点**：`memory_get` 行为对齐（缺失文件返回空文本）；今日/昨日入 context、flush 前写入、memory_vector 重排等配置与 OpenClaw 对齐或留扩展位。

### 若从 OpenClaw 迁到 joyhousebot

- **MEMORY.md + memory/YYYY-MM-DD.md** 可直接复用（路径与语义一致）。
- **memory_search 语义检索**：需在 joyhousebot 侧配置 `memory_backend = mcp_qmd`（或 auto）并接入 QMD，或开启 `memory_vector_enabled` 做重排。
- **无 L0/L1**：不启用 `memory_use_l0`、不跑 compaction 即可；L1/L0 为可选增强。
- **无 P1/P2**：MEMORY 不写 `[P1|expire:...]` 即可；janitor 仅处理带过期标签的行。

---

*文档基于当前代码与 [OpenClaw Memory](https://docs.openclaw.ai/concepts/memory) 文档整理，若 OpenClaw 更新以官方文档为准。*
