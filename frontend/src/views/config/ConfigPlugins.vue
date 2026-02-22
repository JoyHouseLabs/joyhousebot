<template>
  <div class="config-panel" v-if="plugins">
    <div class="config-panel-header">
      <h2 class="config-panel-title">插件</h2>
      <p class="config-panel-desc">启用、加载路径、按插件配置</p>
    </div>
    <n-form label-placement="left" label-width="140" class="config-form">
      <n-form-item label="启用插件">
        <n-switch v-model:value="plugins.enabled" />
      </n-form-item>
      <n-form-item label="OpenClaw 路径">
        <n-input
          v-model:value="plugins.openclaw_dir"
          placeholder="留空使用默认（与 joyhousebot 同级的 openclaw 或环境变量 JOYHOUSEBOT_OPENCLAW_DIR）"
          clearable
        />
      </n-form-item>
      <n-form-item label="Allow 列表">
        <n-dynamic-tags v-model:value="plugins.allow" />
      </n-form-item>
      <n-form-item label="Deny 列表">
        <n-dynamic-tags v-model:value="plugins.deny" />
      </n-form-item>
      <n-form-item label="加载路径">
        <n-dynamic-tags v-model:value="loadPaths" />
      </n-form-item>
      <n-form-item label="Memory Slot">
        <n-input v-model:value="plugins.slots.memory" placeholder="可选" clearable />
      </n-form-item>
    </n-form>
    <h3 class="subsection-title">Entries（按插件启用与 config）</h3>
    <n-collapse>
      <n-collapse-item
        v-for="(entry, id) in (plugins.entries || {})"
        :key="String(id)"
        :title="String(id)"
      >
        <n-form label-placement="left" label-width="80" size="small">
          <n-form-item label="enabled">
            <n-switch v-model:value="(entry as Record<string, unknown>).enabled" />
          </n-form-item>
        </n-form>
        <p class="entry-config-hint">config 为 JSON 对象，可在配置文件中编辑。</p>
      </n-collapse-item>
    </n-collapse>
    <p v-if="!Object.keys(plugins.entries || {}).length" class="empty-hint">暂无插件条目。</p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{ config: ConfigData | null }>()

function ensurePlugins() {
  const c = props.config as Record<string, unknown>
  if (!c) return
  if (!c.plugins || typeof c.plugins !== 'object') {
    c.plugins = {
      enabled: true,
      openclaw_dir: '',
      allow: [],
      deny: [],
      load: { paths: [] },
      entries: {},
      slots: { memory: undefined },
      installs: {},
    }
  }
  const p = c.plugins as Record<string, unknown>
  if (typeof p.openclaw_dir !== 'string') p.openclaw_dir = ''
  if (!p.load || typeof p.load !== 'object') p.load = { paths: [] }
  if (!(p.slots && typeof p.slots === 'object')) p.slots = { memory: undefined }
}

const plugins = computed(() => {
  ensurePlugins()
  return ((props.config as Record<string, unknown>)?.plugins ?? {}) as Record<string, unknown>
})

const loadPaths = computed({
  get() {
    const p = (props.config as Record<string, unknown>)?.plugins as Record<string, unknown>
    const load = p?.load as { paths?: string[] }
    return Array.isArray(load?.paths) ? [...load.paths] : []
  },
  set(val: string[]) {
    ensurePlugins()
    const p = (props.config as Record<string, unknown>)?.plugins as Record<string, unknown>
    const load = p?.load as Record<string, unknown>
    if (load) load.paths = val
  },
})
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
  margin-bottom: 16px;
}
.subsection-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 8px 0;
}
.entry-config-hint {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 4px 0 0 0;
}
.empty-hint {
  font-size: 12px;
  color: var(--n-text-color-3);
  margin: 8px 0 0 0;
}
</style>
