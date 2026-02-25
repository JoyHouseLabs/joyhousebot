<template>
  <div class="config-panel">
    <template v-if="!retrieval">
      <div class="config-panel-header">
        <h2 class="config-panel-title">记忆</h2>
        <p class="config-panel-desc">记忆配置来自「工具 → 检索」；若此处无内容请先保存一次配置或刷新。</p>
      </div>
      <p class="empty-hint">暂无记忆配置项</p>
    </template>
  <div v-else class="config-panel-inner">
    <div class="config-panel-header">
      <h2 class="config-panel-title">记忆</h2>
      <p class="config-panel-desc">新旧记忆切换、L0 与记忆/知识库检索（与「工具 → 检索」同步）</p>
    </div>
    <n-form label-placement="left" label-width="160" class="config-form">
      <n-form-item label="系统提示使用 L0（新记忆）">
        <n-switch
          :value="Boolean(retrieval && retrieval.memory_use_l0)"
          @update:value="(v) => setRetrieval('memory_use_l0', v)"
        />
        <span class="form-hint">开启后系统提示注入 L0 (.abstract) + MEMORY.md；关闭则仅 MEMORY.md（旧记忆）</span>
      </n-form-item>
      <n-form-item label="优先查记忆">
        <n-switch
          :value="Boolean(retrieval && retrieval.memory_first)"
          @update:value="(v) => setRetrieval('memory_first', v)"
        />
        <span class="form-hint">提示 Agent 先查 L0/记忆再查知识库</span>
      </n-form-item>
      <n-form-item label="记忆检索后端">
        <n-select
          :value="(retrieval && 'memory_backend' in retrieval ? retrieval.memory_backend : 'builtin') as string"
          :options="memoryBackendOptions"
          placeholder="builtin"
          style="width: 100%"
          @update:value="(v) => setRetrieval('memory_backend', v)"
        />
        <span class="form-hint">grep / QMD MCP / sqlite_vector / auto</span>
      </n-form-item>
      <n-form-item label="知识库检索后端">
        <n-select
          :value="(retrieval && 'knowledge_backend' in retrieval ? retrieval.knowledge_backend : 'builtin') as string"
          :options="knowledgeBackendOptions"
          placeholder="builtin"
          style="width: 100%"
          @update:value="(v) => setRetrieval('knowledge_backend', v)"
        />
        <span class="form-hint">FTS5+Chroma / QMD MCP（需 knowledge_search 工具）</span>
      </n-form-item>
      <n-form-item label="同步到 QMD 索引">
        <n-switch
          :value="Boolean(retrieval && retrieval.knowledge_qmd_sync_enabled)"
          @update:value="(v) => setRetrieval('knowledge_qmd_sync_enabled', v)"
        />
        <span class="form-hint">pipeline 索引完成后 POST 到 QMD</span>
      </n-form-item>
      <n-form-item label="QMD 同步 URL" v-if="retrieval && retrieval.knowledge_qmd_sync_enabled">
        <n-input
          :value="(retrieval && retrieval.knowledge_qmd_sync_url) || ''"
          placeholder="http://localhost:8181/index"
          style="width: 100%"
          @update:value="(v) => setRetrieval('knowledge_qmd_sync_url', v)"
        />
      </n-form-item>
      <n-form-item label="记忆检索条数">
        <n-input-number
          :value="typeof retrieval?.memory_top_k === 'number' ? retrieval.memory_top_k : 10"
          :min="1"
          :max="50"
          style="width: 100%"
          @update:value="(v) => setRetrieval('memory_top_k', v)"
        />
      </n-form-item>
    </n-form>
  </div>
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
  return (tools?.retrieval as Record<string, unknown> | undefined) ?? null
})

function setRetrieval(key: string, value: unknown) {
  const tools = props.config?.tools as Record<string, unknown> | undefined
  const r = tools?.retrieval as Record<string, unknown> | undefined
  if (r && typeof r === 'object') r[key] = value
}
</script>

<style scoped>
.config-panel-header {
  margin-bottom: 16px;
}
.config-panel-title {
  font-size: 16px;
  font-weight: 600;
  margin: 0 0 4px 0;
}
.config-panel-desc {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 0;
}
.config-form {
  max-width: 520px;
}
.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--n-text-color-3);
}
.empty-hint {
  font-size: 13px;
  color: var(--n-text-color-3);
  margin: 0;
}
</style>
