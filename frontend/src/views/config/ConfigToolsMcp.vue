<template>
  <div class="config-tools-block" v-if="mcpServers">
    <p class="block-desc">MCP 服务器列表（name → command/args/env 或 url）</p>
    <n-collapse>
      <n-collapse-item
        v-for="(entry, name) in mcpServers"
        :key="String(name)"
        :title="String(name)"
        :name="String(name)"
      >
        <n-form label-placement="left" label-width="100" class="config-form compact">
          <n-form-item label="command">
            <n-input v-model:value="entry.command" placeholder="如 npx" />
          </n-form-item>
          <n-form-item label="args">
            <n-dynamic-tags v-model:value="entry.args" />
          </n-form-item>
          <n-form-item label="url">
            <n-input v-model:value="entry.url" placeholder="HTTP 时填写" />
          </n-form-item>
        </n-form>
        <n-button quaternary size="small" type="error" @click="remove(name)">删除</n-button>
      </n-collapse-item>
    </n-collapse>
    <div class="mcp-add">
      <n-input
        v-model:value="newName"
        placeholder="新 MCP 名称"
        style="width: 160px; margin-right: 8px"
        @keyup.enter="add"
      />
      <n-button size="small" @click="add">添加</n-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()
const newName = ref('')

const mcpServers = computed(() => {
  const tools = props.config?.tools as Record<string, unknown> | undefined
  const m = tools?.mcp_servers as Record<string, Record<string, unknown>> | undefined
  if (!m || typeof m !== 'object') return {}
  return m
})

function add() {
  const n = newName.value.trim()
  if (!n) return
  const tools = props.config?.tools as Record<string, unknown>
  if (!tools) return
  let m = tools.mcp_servers as Record<string, Record<string, unknown>>
  if (!m || typeof m !== 'object') {
    m = {}
    tools.mcp_servers = m
  }
  if (!m[n]) {
    m[n] = { command: '', args: [], env: {}, url: '' }
  }
  newName.value = ''
}

function remove(name: string) {
  const tools = props.config?.tools as Record<string, unknown>
  const m = tools?.mcp_servers as Record<string, unknown>
  if (m && typeof m === 'object') delete m[name]
}
</script>

<style scoped>
.block-desc {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 0 0 12px 0;
}
.config-form.compact {
  max-width: 480px;
}
.mcp-add {
  margin-top: 12px;
  display: flex;
  align-items: center;
}
</style>
