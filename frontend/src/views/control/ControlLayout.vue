<template>
  <div class="control-wrap">
    <div class="control-status-bar" v-if="showStatusBar">
      <div class="status-row">
        <n-tag :type="gateway.connected ? 'success' : 'error'" size="small" round>
          {{ gateway.connected ? 'WS 已连接' : 'WS 未连接' }}
        </n-tag>
        <span v-if="gateway.hello?.auth?.scopes?.length" class="scopes-text">
          权限: {{ (gateway.hello.auth.scopes as string[]).join(', ') }}
        </span>
        <span v-if="scopeHints.length" class="scope-hints">
          <n-tag v-for="h in scopeHints" :key="h" type="warning" size="small">{{ h }}</n-tag>
        </span>
        <span v-if="gateway.lastError" class="status-error">{{ gateway.lastError }}</span>
      </div>
    </div>
    <router-view />
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { NTag } from 'naive-ui'
import { useGatewayInject } from '../../composables/useGateway'

const gateway = useGatewayInject()
if (!gateway) {
  throw new Error('ControlLayout requires GatewayKey from ShellLayout')
}

const showStatusBar = computed(() => true)

const scopeHints = computed(() => {
  const scopes = (gateway.hello.value?.auth?.scopes as string[] | undefined) ?? []
  const hints: string[] = []
  if (!scopes.includes('operator.approvals')) hints.push('缺少 operator.approvals（无法处理审批）')
  if (!scopes.includes('operator.pairing')) hints.push('缺少 operator.pairing（无法管理配对）')
  return hints
})
</script>

<style scoped>
.control-wrap {
  padding: 0;
  min-height: 0;
}
.control-status-bar {
  padding: 8px 16px;
  background: var(--n-color-modal, #f5f5f5);
  border-bottom: 1px solid var(--n-border-color, #eee);
  font-size: 12px;
}
.status-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.scopes-text { color: var(--text-muted, #666); }
.scope-hints { display: inline-flex; gap: 6px; }
.status-error { color: var(--n-color-error); }
</style>
