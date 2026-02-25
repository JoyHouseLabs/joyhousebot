<template>
  <div class="config-tools-block" v-if="retrieval">
    <n-form label-placement="left" label-width="160" class="config-form">
      <n-form-item label="启用向量检索">
        <n-switch v-model:value="retrieval.vector_enabled" />
      </n-form-item>
      <n-form-item label="向量阈值 (chunks)">
        <n-input-number v-model:value="retrieval.vector_threshold_chunks" :min="0" style="width: 100%" />
      </n-form-item>
      <n-form-item label="Embedding 提供商">
        <n-input v-model:value="retrieval.embedding_provider" placeholder="如 openai" />
      </n-form-item>
      <n-form-item label="Embedding 模型">
        <n-input v-model:value="retrieval.embedding_model" placeholder="如 text-embedding-3-small" />
      </n-form-item>
      <n-form-item label="向量后端">
        <n-input v-model:value="retrieval.vector_backend" placeholder="chroma | qdrant | pgvector" />
      </n-form-item>
      <n-form-item label="记忆检索后端">
        <n-select
          :value="(retrieval && 'memory_backend' in retrieval ? retrieval.memory_backend : 'builtin') as string"
          :options="memoryBackendOptions"
          placeholder="builtin"
          style="width: 100%"
          @update:value="(v) => retrieval && ((retrieval as Record<string, unknown>).memory_backend = v)"
        />
      </n-form-item>
      <n-form-item label="知识库检索后端">
        <n-select
          :value="(retrieval && 'knowledge_backend' in retrieval ? retrieval.knowledge_backend : 'builtin') as string"
          :options="knowledgeBackendOptions"
          placeholder="builtin"
          style="width: 100%"
          @update:value="(v) => retrieval && ((retrieval as Record<string, unknown>).knowledge_backend = v)"
        />
        <span class="form-hint">builtin=FTS5+Chroma；qmd/auto=QMD MCP（需提供 knowledge_search 工具）</span>
      </n-form-item>
      <n-form-item label="同步到 QMD 索引">
        <n-switch
          :value="Boolean(retrieval && retrieval.knowledge_qmd_sync_enabled)"
          @update:value="(v) => retrieval && ((retrieval as Record<string, unknown>).knowledge_qmd_sync_enabled = v)"
        />
        <span class="form-hint">开启后 pipeline 索引完成后 POST 到下方 URL</span>
      </n-form-item>
      <n-form-item label="QMD 同步 URL" v-if="retrieval?.knowledge_qmd_sync_enabled">
        <n-input
          v-model:value="retrieval.knowledge_qmd_sync_url"
          placeholder="http://localhost:8181/index 或 QMD 索引接口"
          style="width: 100%"
        />
      </n-form-item>
      <n-form-item label="系统提示使用 L0">
        <n-switch
          :value="Boolean(retrieval && retrieval.memory_use_l0)"
          @update:value="(v) => retrieval && ((retrieval as Record<string, unknown>).memory_use_l0 = v)"
        />
        <span class="form-hint">开启后系统提示注入 L0 + MEMORY.md（新旧记忆切换）</span>
      </n-form-item>
      <n-form-item label="优先查记忆">
        <n-switch
          :value="Boolean(retrieval && retrieval.memory_first)"
          @update:value="(v) => retrieval && ((retrieval as Record<string, unknown>).memory_first = v)"
        />
        <span class="form-hint">提示 Agent 先查 L0/记忆再查知识库</span>
      </n-form-item>
      <n-form-item label="记忆检索条数">
        <n-input-number
          :value="typeof retrieval?.memory_top_k === 'number' ? retrieval.memory_top_k : 10"
          :min="1"
          :max="50"
          style="width: 100%"
          @update:value="(v) => retrieval && ((retrieval as Record<string, unknown>).memory_top_k = v)"
        />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

const memoryBackendOptions = [
  { label: 'builtin（grep）', value: 'builtin' },
  { label: 'mcp_qmd（QMD MCP）', value: 'mcp_qmd' },
  { label: 'sqlite_vector（SQLite+向量）', value: 'sqlite_vector' },
  { label: 'auto（QMD → sqlite_vector → grep）', value: 'auto' },
]

const knowledgeBackendOptions = [
  { label: 'builtin（FTS5+Chroma）', value: 'builtin' },
  { label: 'qmd（QMD MCP）', value: 'qmd' },
  { label: 'auto（先 QMD 再 builtin）', value: 'auto' },
]

const retrieval = computed(() => {
  const tools = props.config?.tools as Record<string, unknown> | undefined
  const r = tools?.retrieval as Record<string, unknown> | undefined
  if (!r) return null
  return r
})
</script>

<style scoped>
.config-form {
  max-width: 520px;
}
.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--n-text-color-3);
}
</style>
