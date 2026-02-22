<template>
  <div class="config-panel" v-if="config?.tools !== undefined">
    <div class="config-panel-header">
      <h2 class="config-panel-title">工具</h2>
      <p class="config-panel-desc">Web 搜索、执行、代码运行、检索、摄入、MCP 等</p>
    </div>
    <n-form label-placement="left" label-width="140" class="config-form top-form">
      <n-form-item label="限制到工作区">
        <n-switch v-model:value="restrictToWorkspace" />
        <span class="form-hint">开启后工具仅能访问工作区目录</span>
      </n-form-item>
    </n-form>
    <n-collapse class="config-tools-collapse">
      <n-collapse-item title="Web 搜索" name="web">
        <ConfigToolsWeb :config="config" />
      </n-collapse-item>
      <n-collapse-item title="执行 (exec)" name="exec">
        <ConfigToolsExec :config="config" />
      </n-collapse-item>
      <n-collapse-item title="代码运行 (code_runner)" name="code_runner">
        <ConfigToolsCodeRunner :config="config" />
      </n-collapse-item>
      <n-collapse-item title="检索 (retrieval)" name="retrieval">
        <ConfigToolsRetrieval :config="config" />
      </n-collapse-item>
      <n-collapse-item title="摄入 (ingest)" name="ingest">
        <ConfigToolsIngest :config="config" />
      </n-collapse-item>
      <n-collapse-item title="可选工具白名单" name="optional_allowlist">
        <ConfigToolsOptionalAllowlist :config="config" />
      </n-collapse-item>
      <n-collapse-item title="MCP 服务器" name="mcp">
        <ConfigToolsMcp :config="config" />
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'
import ConfigToolsWeb from './ConfigToolsWeb.vue'
import ConfigToolsExec from './ConfigToolsExec.vue'
import ConfigToolsCodeRunner from './ConfigToolsCodeRunner.vue'
import ConfigToolsRetrieval from './ConfigToolsRetrieval.vue'
import ConfigToolsIngest from './ConfigToolsIngest.vue'
import ConfigToolsOptionalAllowlist from './ConfigToolsOptionalAllowlist.vue'
import ConfigToolsMcp from './ConfigToolsMcp.vue'

const props = defineProps<{
  config: ConfigData | null
}>()

const restrictToWorkspace = computed({
  get() {
    const t = props.config?.tools as Record<string, unknown> | undefined
    return Boolean(t?.restrict_to_workspace)
  },
  set(v: boolean) {
    const t = props.config?.tools as Record<string, unknown>
    if (t) t.restrict_to_workspace = v
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
.top-form {
  max-width: 520px;
  margin-bottom: 16px;
}
.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--n-text-color-3);
}
.config-tools-collapse {
  max-width: 640px;
}
</style>
