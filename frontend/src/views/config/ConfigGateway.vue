<template>
  <div class="config-panel" v-if="gateway">
    <div class="config-panel-header">
      <h2 class="config-panel-title">Gateway</h2>
      <p class="config-panel-desc">控制连接、RPC、节点策略等</p>
    </div>
    <n-form label-placement="left" label-width="200" class="config-form">
      <n-form-item label="Host">
        <n-input v-model:value="gateway.host" placeholder="0.0.0.0" />
      </n-form-item>
      <n-form-item label="Port">
        <n-input-number v-model:value="gateway.port" :min="1" :max="65535" style="width: 100%" />
      </n-form-item>
      <n-form-item label="Control Token">
        <n-input
          v-model:value="gateway.control_token"
          type="password"
          show-password-on="click"
          placeholder="连接控制台时使用的 token"
          clearable
        />
      </n-form-item>
      <n-form-item label="Control Password（可选）">
        <n-input
          v-model:value="gateway.control_password"
          type="password"
          show-password-on="click"
          placeholder="替代 token 的密码"
          clearable
        />
      </n-form-item>
      <n-form-item label="允许无认证连接（仅开发）">
        <n-switch
          :value="controlUiAllowInsecure"
          @update:value="onControlUiAllowInsecure"
        />
      </n-form-item>
      <n-form-item label="RPC 启用">
        <n-switch v-model:value="gateway.rpc_enabled" />
      </n-form-item>
      <n-form-item label="RPC 影子读">
        <n-switch v-model:value="gateway.rpc_shadow_reads" />
      </n-form-item>
      <n-form-item label="会话串行化">
        <n-switch v-model:value="gateway.chat_session_serialization" />
        <span class="form-hint">同一会话一次只跑一个请求</span>
      </n-form-item>
      <n-form-item label="单会话队列最大待处理数">
        <n-input-number v-model:value="gateway.max_lane_pending" :min="1" style="width: 100%" />
      </n-form-item>
      <n-form-item label="轨迹步骤 payload 最大字符">
        <n-input-number
          :value="traceMaxValue"
          placeholder="不截断"
          :min="1"
          clearable
          style="width: 100%"
          @update:value="onTraceMaxChange"
        />
      </n-form-item>
      <n-form-item label="节点浏览器模式">
        <n-select
          v-model:value="gateway.node_browser_mode"
          :options="nodeBrowserModeOptions"
          style="width: 100%"
        />
      </n-form-item>
      <n-form-item label="节点浏览器目标">
        <n-input v-model:value="gateway.node_browser_target" placeholder="留空" clearable />
      </n-form-item>
      <n-form-item label="允许的命令（节点）">
        <n-dynamic-tags v-model:value="gateway.node_allow_commands" />
      </n-form-item>
      <n-form-item label="拒绝的命令（节点）">
        <n-dynamic-tags v-model:value="gateway.node_deny_commands" />
      </n-form-item>
    </n-form>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { ConfigData } from '../../api/config'

const props = defineProps<{
  config: ConfigData | null
}>()

const gateway = computed(() => {
  const g = props.config?.gateway as Record<string, unknown> | undefined
  if (!g) return null
  if (!Array.isArray(g.node_allow_commands)) g.node_allow_commands = []
  if (!Array.isArray(g.node_deny_commands)) g.node_deny_commands = []
  return g
})

const nodeBrowserModeOptions = [
  { label: 'auto', value: 'auto' },
  { label: 'manual', value: 'manual' },
  { label: 'off', value: 'off' },
]

const controlUiAllowInsecure = computed(() => {
  const ui = (props.config?.gateway as Record<string, unknown>)?.control_ui
  return Boolean(ui && typeof ui === 'object' && (ui as Record<string, unknown>).allow_insecure_auth)
})

function onControlUiAllowInsecure(val: boolean) {
  const g = props.config?.gateway as Record<string, unknown>
  if (!g) return
  g.control_ui = { allow_insecure_auth: val }
}

const traceMaxValue = computed(() => {
  const v = (props.config?.gateway as Record<string, unknown>)?.trace_max_step_payload_chars
  return v === undefined || v === null ? null : Number(v)
})

function onTraceMaxChange(val: number | null) {
  const g = props.config?.gateway as Record<string, unknown>
  if (!g) return
  g.trace_max_step_payload_chars = val ?? null
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
  max-width: 560px;
}
.form-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--n-text-color-3);
}
</style>
